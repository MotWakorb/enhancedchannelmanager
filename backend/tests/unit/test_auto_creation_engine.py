"""
Unit tests for the auto-creation engine service.

Tests the AutoCreationEngine class which orchestrates the entire auto-creation
pipeline, coordinating rules, streams, and executions.
"""
from unittest.mock import MagicMock, AsyncMock, patch
import asyncio

from auto_creation_engine import (
    AutoCreationEngine,
    get_auto_creation_engine,
    set_auto_creation_engine,
    init_auto_creation_engine,
    _sort_key,
    _smart_sort_streams,
    _sort_streams_by_m3u_account_priority,
    _sort_streams_by_resolution_height,
    _reorder_streams_for_rule,
)
from auto_creation_evaluator import StreamContext
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
        self.client.get_channels = AsyncMock(return_value={
            "count": 2,
            "results": [
                {"id": 1, "name": "ESPN"},
                {"id": 2, "name": "CNN"},
            ]
        })
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
        self.client.get_channels.assert_called_once_with(page=1, page_size=100)
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
        self.client.get_channels = AsyncMock(return_value={"count": 0, "results": []})
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
        self.client.get_streams = AsyncMock(return_value={
            "count": 2,
            "results": [
                {"id": 101, "name": "ESPN HD", "group_title": "Sports"},
                {"id": 102, "name": "CNN HD", "group_title": "News"},
            ]
        })
        self.engine = AutoCreationEngine(self.client)
        # Pre-populate existing groups so _fetch_streams doesn't need them unset
        self.engine._existing_groups = []

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

        self.client.get_streams = AsyncMock(side_effect=Exception("API error"))

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
        self.client.get_channels = AsyncMock(return_value={"count": 0, "results": []})
        self.client.get_channel_groups = AsyncMock(return_value=[])
        self.client.get_m3u_accounts = AsyncMock(return_value=[
            {"id": 1, "name": "Provider A"},
        ])
        self.client.get_streams = AsyncMock(return_value={
            "count": 1,
            "results": [
                {"id": 101, "name": "ESPN HD", "group_title": "Sports"},
            ]
        })
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

    @patch("auto_creation_engine.get_session")
    def test_process_streams_no_match(self, mock_get_session):
        """Process streams with no matching rules."""
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session

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

    @patch("auto_creation_engine.get_session")
    def test_process_streams_match_skip(self, mock_get_session):
        """Process streams that match a skip rule."""
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session

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

    @patch("auto_creation_engine.get_session")
    def test_process_streams_stop_processing(self, mock_get_session):
        """Process streams stops on stop_processing action."""
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session

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

        # Both streams are evaluated in Pass 1, but stop_processing
        # halts Pass 2 after the first match is actioned
        assert result["streams_evaluated"] == 2
        assert result["streams_matched"] == 1


class TestPass3RenumberGating:
    """
    Pass 3 (channel renumber) gating — bd-yj5yi / GH-104 regression.

    PR #107 added a normalized-name fallback in _find_channel_by_name that
    lets auto-creation find MORE pre-existing channels for incoming streams.
    When if_exists=skip|merge matches a channel in a foreign group, the old
    code unconditionally added that channel to rule_channel_order, and Pass 3
    (for any rule with sort_field) then called assign_channel_numbers() on
    the expanded list — renumbering channels the rule never owned.

    These tests exercise the gating logic on the Pass 3 append:
    - owned channels (just created OR pre-run managed) are renumbered
    - foreign/unmanaged channels matched via fallback are NOT renumbered.
    """

    def setup_method(self):
        """Set up test fixtures."""
        from auto_creation_executor import ActionResult, ExecutionContext

        self.ActionResult = ActionResult
        self.ExecutionContext = ExecutionContext

        self.client = MagicMock()
        self.client.assign_channel_numbers = AsyncMock()
        self.client.get_channels = AsyncMock(return_value={"count": 0, "results": []})
        self.engine = AutoCreationEngine(self.client)
        self.engine._existing_channels = []
        self.engine._existing_groups = []

    def _make_rule(self, rule_id, name, sort_field=None, starting_channel_number=None,
                   managed_channel_ids=None):
        """Build a mock rule with reasonable defaults."""
        rule = MagicMock()
        rule.id = rule_id
        rule.name = name
        rule.priority = 0
        rule.m3u_account_id = None
        rule.target_group_id = None
        rule.enabled = True
        rule.stop_on_first_match = True
        rule.skip_struck_streams = False
        rule.sort_field = sort_field
        rule.sort_order = "asc"
        rule.sort_regex = None
        rule.starting_channel_number = starting_channel_number
        rule.orphan_action = "none"
        rule.managed_channel_ids = None if managed_channel_ids is None else "[]"
        rule.get_managed_channel_ids.return_value = managed_channel_ids or []
        rule.get_conditions.return_value = [{"type": "always"}]
        # action carries the numbering spec (range start is the renumber anchor)
        _action = {"type": "create_channel", "params": {}}
        if starting_channel_number is not None:
            _action["channel_number"] = starting_channel_number
        rule.get_actions.return_value = [_action]
        rule.get_normalization_group_ids.return_value = []
        rule.match_scope_target_group = False
        return rule

    def _make_execute_fn(self, channel_id, *, created):
        """
        Build an executor.execute replacement that simulates either
        a successful channel create OR a fallback-match (skip/merge) into
        a pre-existing foreign channel.
        """
        async def _fake_execute(action, stream_ctx, exec_ctx,
                                rule_target_group_id=None,
                                normalization_group_ids=None,
                                match_scope_target_group=False):
            exec_ctx.current_channel_id = channel_id
            if created:
                exec_ctx.created_channel_ids.add(channel_id)
                exec_ctx.channels_created += 1
            return self.ActionResult(
                success=True,
                action_type="create_channel",
                description=f"{'Created' if created else 'Matched-existing'} channel id={channel_id}",
                entity_type="channel",
                entity_id=channel_id,
                entity_name=f"ch-{channel_id}",
                created=created,
            )
        return _fake_execute

    @patch("auto_creation_engine.get_session")
    def test_does_not_renumber_foreign_channel_matched_via_fallback(self, mock_get_session):
        """
        Rule B (sort_field=name) with no pre-run managed channels. Stream matches
        into a pre-existing foreign channel (e.g., owned by Rule A). current_channel_id
        is set but created=False. Pass 3 must NOT call assign_channel_numbers on
        the foreign channel.
        """
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session

        streams = [
            StreamContext(stream_id=101, stream_name="ESPN", m3u_account_id=1, m3u_account_name="P"),
            StreamContext(stream_id=102, stream_name="ESPN 2", m3u_account_id=1, m3u_account_name="P"),
        ]
        rule_b = self._make_rule(
            rule_id=2, name="Rule B",
            sort_field="name", starting_channel_number=4000,
            managed_channel_ids=[],  # rule owns nothing yet
        )

        # Both streams fall into fallback-match on foreign channels 501 & 502
        # (created by some other rule's earlier run). Simulate by returning
        # created=False and setting current_channel_id without adding to created set.
        execute_fns = iter([
            self._make_execute_fn(501, created=False),
            self._make_execute_fn(502, created=False),
        ])

        async def dispatch_execute(*args, **kwargs):
            fn = next(execute_fns)
            return await fn(*args, **kwargs)

        mock_execution = MagicMock()
        mock_execution.id = 1

        with patch("auto_creation_engine.ActionExecutor") as mock_exec_cls:
            mock_executor = MagicMock()
            mock_executor.execute = AsyncMock(side_effect=dispatch_execute)
            mock_executor.verify_epg_assignments = AsyncMock(return_value=(0, 0, 0))
            mock_executor.prune_merge_streams = AsyncMock()
            mock_executor.reorder_streams_on_channels = AsyncMock(return_value=0)
            mock_executor._channel_by_id = {}
            mock_executor._created_channels = {}
            mock_exec_cls.return_value = mock_executor

            # Stub engine internals that touch DB/external calls
            self.engine._refresh_dummy_epg_and_retry = AsyncMock()
            self.engine._reconcile_orphans = AsyncMock()
            self.engine._update_rule_stats = AsyncMock()

            asyncio.get_event_loop().run_until_complete(
                self.engine._process_streams(streams, [rule_b], mock_execution, dry_run=False)
            )

        # Foreign channels 501/502 must NOT be renumbered by Rule B.
        # If assign_channel_numbers was called at all, the regression is present.
        assert self.client.assign_channel_numbers.await_count == 0, (
            "Rule B renumbered a foreign channel it did not own "
            f"(call args: {self.client.assign_channel_numbers.await_args_list})"
        )

    @patch("auto_creation_engine.get_session")
    def test_stream_reorder_runs_on_modified_existing_channel(self, mock_get_session):
        """
        Pass 3.5 gating: when a rule merges streams into an existing channel
        (created=0), stream sorting should still run for that channel.
        """
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session

        streams = [
            StreamContext(stream_id=201, stream_name="A", m3u_account_id=1, m3u_account_name="P"),
            StreamContext(stream_id=202, stream_name="B", m3u_account_id=1, m3u_account_name="P"),
        ]

        rule = self._make_rule(
            rule_id=2, name="Rule",
            sort_field=None, starting_channel_number=None,
            managed_channel_ids=[],  # not pre-run managed
        )
        rule.stream_sort_field = "quality"
        rule.stream_sort_order = "desc"

        # Simulate merge into existing channel 501 (not created).
        async def _fake_execute(action, stream_ctx, exec_ctx,
                                rule_target_group_id=None,
                                normalization_group_ids=None,
                                match_scope_target_group=False):
            exec_ctx.current_channel_id = 501
            return self.ActionResult(
                success=True,
                action_type="merge_stream",
                description="Added stream",
                entity_type="channel",
                entity_id=501,
                entity_name="ch-501",
                modified=True,
                created=False,
            )

        mock_execution = MagicMock()
        mock_execution.id = 1

        with patch("auto_creation_engine.ActionExecutor") as mock_exec_cls, \
             patch.object(self.engine, "_reorder_channel_streams", new_callable=AsyncMock) as mock_reorder:
            mock_executor = MagicMock()
            mock_executor.execute = AsyncMock(side_effect=_fake_execute)
            mock_executor.verify_epg_assignments = AsyncMock(return_value=(0, 0, 0))
            mock_executor.prune_merge_streams = AsyncMock()
            mock_executor._channel_by_id = {}
            mock_executor._created_channels = {}
            mock_exec_cls.return_value = mock_executor

            # Stub engine internals that touch DB/external calls
            self.engine._refresh_dummy_epg_and_retry = AsyncMock()
            self.engine._reconcile_orphans = AsyncMock()
            self.engine._update_rule_stats = AsyncMock()

            asyncio.get_event_loop().run_until_complete(
                self.engine._process_streams(streams, [rule], mock_execution, dry_run=False)
            )

        # Pass 3.5 should have been invoked with a channel list containing 501.
        assert mock_reorder.await_count == 1
        passed_rule_channel_order = mock_reorder.await_args.args[1]
        assert passed_rule_channel_order.get(rule.id) == [501]

    @patch("auto_creation_engine._auto_rename_after_renumber", new_callable=AsyncMock)
    @patch("auto_creation_engine.get_session")
    def test_renumbers_own_created_channels(self, mock_get_session, mock_rename):
        """
        Rule creates 2 new channels with sort_field=name set. Pass 3 should
        renumber those 2 channels starting at the rule's starting_channel_number.
        """
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session

        streams = [
            StreamContext(stream_id=101, stream_name="ESPN A", m3u_account_id=1, m3u_account_name="P"),
            StreamContext(stream_id=102, stream_name="ESPN B", m3u_account_id=1, m3u_account_name="P"),
        ]
        rule_a = self._make_rule(
            rule_id=1, name="Rule A",
            sort_field="name", starting_channel_number=100,
            managed_channel_ids=[],
        )

        execute_fns = iter([
            self._make_execute_fn(201, created=True),
            self._make_execute_fn(202, created=True),
        ])

        async def dispatch_execute(*args, **kwargs):
            fn = next(execute_fns)
            return await fn(*args, **kwargs)

        mock_execution = MagicMock()
        mock_execution.id = 1

        with patch("auto_creation_engine.ActionExecutor") as mock_exec_cls:
            mock_executor = MagicMock()
            mock_executor.execute = AsyncMock(side_effect=dispatch_execute)
            mock_executor.verify_epg_assignments = AsyncMock(return_value=(0, 0, 0))
            mock_executor.prune_merge_streams = AsyncMock()
            mock_executor.reorder_streams_on_channels = AsyncMock(return_value=0)
            mock_executor._channel_by_id = {}
            mock_executor._created_channels = {}
            mock_exec_cls.return_value = mock_executor

            # Stub engine internals that touch DB/external calls
            self.engine._refresh_dummy_epg_and_retry = AsyncMock()
            self.engine._reconcile_orphans = AsyncMock()
            self.engine._update_rule_stats = AsyncMock()

            asyncio.get_event_loop().run_until_complete(
                self.engine._process_streams(streams, [rule_a], mock_execution, dry_run=False)
            )

        # Rule A's own created channels (201, 202) get renumbered at 100.
        self.client.assign_channel_numbers.assert_awaited()
        call_args = self.client.assign_channel_numbers.await_args_list[0]
        assert call_args.args[0] == [201, 202]
        assert call_args.args[1] == 100

    @patch("auto_creation_engine._auto_rename_after_renumber", new_callable=AsyncMock)
    @patch("auto_creation_engine.get_session")
    def test_renumbers_previously_managed_channels(self, mock_get_session, mock_rename):
        """
        Re-run scenario: rule already owns channels 301/302 (in its
        managed_channel_ids). On re-run, those channels are matched via
        fallback (created=False) but ARE in the pre-run managed set — so
        they SHOULD be renumbered.
        """
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session

        streams = [
            StreamContext(stream_id=101, stream_name="ESPN", m3u_account_id=1, m3u_account_name="P"),
            StreamContext(stream_id=102, stream_name="ESPN 2", m3u_account_id=1, m3u_account_name="P"),
        ]
        rule_c = self._make_rule(
            rule_id=3, name="Rule C",
            sort_field="name", starting_channel_number=4000,
            managed_channel_ids=[301, 302],  # rule already owns these
        )

        execute_fns = iter([
            self._make_execute_fn(301, created=False),
            self._make_execute_fn(302, created=False),
        ])

        async def dispatch_execute(*args, **kwargs):
            fn = next(execute_fns)
            return await fn(*args, **kwargs)

        mock_execution = MagicMock()
        mock_execution.id = 1

        with patch("auto_creation_engine.ActionExecutor") as mock_exec_cls:
            mock_executor = MagicMock()
            mock_executor.execute = AsyncMock(side_effect=dispatch_execute)
            mock_executor.verify_epg_assignments = AsyncMock(return_value=(0, 0, 0))
            mock_executor.prune_merge_streams = AsyncMock()
            mock_executor.reorder_streams_on_channels = AsyncMock(return_value=0)
            mock_executor._channel_by_id = {}
            mock_executor._created_channels = {}
            mock_exec_cls.return_value = mock_executor

            # Stub engine internals that touch DB/external calls
            self.engine._refresh_dummy_epg_and_retry = AsyncMock()
            self.engine._reconcile_orphans = AsyncMock()
            self.engine._update_rule_stats = AsyncMock()

            asyncio.get_event_loop().run_until_complete(
                self.engine._process_streams(streams, [rule_c], mock_execution, dry_run=False)
            )

        # Rule C's previously-managed channels get renumbered (valid re-run behavior).
        self.client.assign_channel_numbers.assert_awaited()
        call_args = self.client.assign_channel_numbers.await_args_list[0]
        assert call_args.args[0] == [301, 302]
        assert call_args.args[1] == 4000


class TestAutoCreationEngineStreamReorderLogging:
    """Tests for Pass 3.5 stream reorder logging."""

    def setup_method(self):
        self.client = MagicMock()
        self.client.update_channel = AsyncMock()
        self.engine = AutoCreationEngine(self.client)

    @patch("auto_creation_engine._reorder_streams_for_rule")
    def test_reorder_logs_when_order_unchanged(self, mock_reorder):
        """If a channel is already sorted, still record a log entry for UI visibility."""
        rule = MagicMock()
        rule.id = 1
        rule.name = "Rule 1"
        rule.stream_sort_field = "smart_sort"
        rule.stream_sort_order = "asc"

        channel_id = 123
        current = [10, 20]
        self.engine._existing_channels = [{"id": channel_id, "name": "Ch 123", "streams": current}]

        mock_reorder.return_value = current  # unchanged

        results = {"execution_log": [], "dry_run_results": []}
        asyncio.get_event_loop().run_until_complete(
            self.engine._reorder_channel_streams(
                rules=[rule],
                rule_channel_order={1: [channel_id]},
                results=results,
                dry_run=False,
                settings=MagicMock(),
                stream_m3u_map={},
            )
        )

        self.client.update_channel.assert_not_called()
        assert len(results["execution_log"]) == 1
        action = results["execution_log"][0]["actions_executed"][0]
        assert action["type"] == "reorder_streams"
        assert action["success"] is True
        assert "already sorted" in action["description"]


class TestAutoCreationEngineStreamReorderUsesChannelNames:
    """Regression: name-based stream sorting should work without probe stats rows."""

    def setup_method(self):
        self.client = MagicMock()
        self.client.update_channel = AsyncMock()
        self.engine = AutoCreationEngine(self.client)
        self.engine._stream_stats_cache = {}  # no probe stats

    def test_stream_name_sort_uses_channel_stream_names(self):
        rule = MagicMock()
        rule.id = 1
        rule.name = "Rule 1"
        rule.stream_sort_field = "stream_name"
        rule.stream_sort_order = "asc"

        channel_id = 10
        # Unsorted by name: Bravo, Alpha
        self.engine._existing_channels = [{
            "id": channel_id,
            "name": "Test Channel",
            "streams": [{"id": 2, "name": "Bravo"}, {"id": 1, "name": "Alpha"}],
        }]

        results = {"execution_log": [], "dry_run_results": []}
        asyncio.get_event_loop().run_until_complete(
            self.engine._reorder_channel_streams(
                rules=[rule],
                rule_channel_order={1: [channel_id]},
                results=results,
                dry_run=False,
                settings=MagicMock(),
                stream_m3u_map={},
            )
        )

        # Should reorder to Alpha, Bravo (ids 1,2) and persist via API.
        self.client.update_channel.assert_awaited_once_with(channel_id, {"streams": [1, 2]})


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

        asyncio.get_event_loop().run_until_complete(
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
        self.client.get_channels = AsyncMock(return_value={
            "count": 1,
            "results": [
                {"id": 1, "name": "ESPN", "channel_number": 100, "streams": [101]},
            ]
        })
        self.client.get_channel_groups = AsyncMock(return_value=[
            {"id": 1, "name": "Sports"},
        ])
        self.client.get_m3u_accounts = AsyncMock(return_value=[
            {"id": 1, "name": "Provider A"},
        ])
        self.client.get_streams = AsyncMock(return_value={
            "count": 2,
            "results": [
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
            ]
        })
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


class TestStreamReorderByRule:
    """Tests for Pass 3.5 reorder respecting rule.stream_sort_field."""

    def test_m3u_account_priority_desc(self):
        """Higher M3U priority value sorts first when order is desc."""
        settings = MagicMock()
        settings.m3u_account_priorities = {"1": 10, "2": 6}
        stream_m3u_map = {100: 2, 101: 1}
        out = _sort_streams_by_m3u_account_priority(
            [100, 101], stream_m3u_map, settings, "desc", "Test"
        )
        assert out == [101, 100]

    def test_m3u_account_priority_asc(self):
        """Lower M3U priority value sorts first when order is asc."""
        settings = MagicMock()
        settings.m3u_account_priorities = {"1": 10, "2": 6}
        stream_m3u_map = {100: 2, 101: 1}
        out = _sort_streams_by_m3u_account_priority(
            [100, 101], stream_m3u_map, settings, "asc", "Test"
        )
        assert out == [100, 101]

    def test_reorder_streams_for_rule_uses_provider_order(self):
        rule = MagicMock()
        rule.stream_sort_field = "provider_order"
        rule.stream_sort_order = "desc"
        settings = MagicMock()
        settings.m3u_account_priorities = {"1": 10, "2": 6}
        stream_m3u_map = {100: 2, 101: 1}
        out = _reorder_streams_for_rule(
            [100, 101], rule, {}, stream_m3u_map, "Ch", settings
        )
        assert out == [101, 100]


class TestQualitySortDeprioritization:
    """Quality (resolution) sort should respect deprioritization settings."""

    def test_quality_sort_pushes_black_screen_below_good_streams(self):
        settings = MagicMock()
        settings.deprioritize_failed_streams = True
        settings.failed_stream_sort_order = ["black_screen", "low_fps", "failed"]

        # Stream 1: 1080p but black screen
        # Stream 2: 720p good
        stats_cache = {
            1: {"resolution": "1920x1080", "probe_status": "success", "is_black_screen": True, "is_low_fps": False},
            2: {"resolution": "1280x720", "probe_status": "success", "is_black_screen": False, "is_low_fps": False},
        }
        out = _sort_streams_by_resolution_height([1, 2], stats_cache, settings, "desc", "Ch")
        assert out == [2, 1]

    def test_quality_sort_same_resolution_m3u_tie_break_desc(self):
        """Equal resolution: higher ECM M3U priority sorts first when tie-break is desc."""
        settings = MagicMock()
        settings.deprioritize_failed_streams = False
        settings.m3u_account_priorities = {"1": 10, "2": 5}
        stats_cache = {
            201: {"resolution": "1920x1080", "probe_status": "success"},
            202: {"resolution": "1920x1080", "probe_status": "success"},
        }
        stream_m3u_map = {201: 2, 202: 1}
        out = _sort_streams_by_resolution_height(
            [201, 202],
            stats_cache,
            settings,
            "desc",
            "Ch",
            stream_m3u_map=stream_m3u_map,
            quality_tie_break_order="desc",
            quality_m3u_tie_break_enabled=True,
        )
        assert out == [202, 201]

    def test_quality_sort_same_resolution_m3u_tie_break_disabled(self):
        """Equal resolution with M3U tie-break off: order by stream id only."""
        settings = MagicMock()
        settings.deprioritize_failed_streams = False
        settings.m3u_account_priorities = {"1": 10, "2": 5}
        stats_cache = {
            201: {"resolution": "1920x1080", "probe_status": "success"},
            202: {"resolution": "1920x1080", "probe_status": "success"},
        }
        stream_m3u_map = {201: 2, 202: 1}
        out = _sort_streams_by_resolution_height(
            [202, 201],
            stats_cache,
            settings,
            "desc",
            "Ch",
            stream_m3u_map=stream_m3u_map,
            quality_tie_break_order="desc",
            quality_m3u_tie_break_enabled=False,
        )
        assert out == [201, 202]

    def test_quality_sort_same_resolution_m3u_tie_break_asc(self):
        """Equal resolution: lower ECM M3U priority sorts first when tie-break is asc."""
        settings = MagicMock()
        settings.deprioritize_failed_streams = False
        settings.m3u_account_priorities = {"1": 10, "2": 5}
        stats_cache = {
            201: {"resolution": "1920x1080", "probe_status": "success"},
            202: {"resolution": "1920x1080", "probe_status": "success"},
        }
        stream_m3u_map = {201: 2, 202: 1}
        out = _sort_streams_by_resolution_height(
            [201, 202],
            stats_cache,
            settings,
            "desc",
            "Ch",
            stream_m3u_map=stream_m3u_map,
            quality_tie_break_order="asc",
            quality_m3u_tie_break_enabled=True,
        )
        assert out == [201, 202]

    def test_reorder_quality_respects_rule_tie_break_via_engine(self):
        rule = MagicMock()
        rule.stream_sort_field = "quality"
        rule.stream_sort_order = "desc"
        rule.quality_tie_break_order = "asc"
        rule.quality_m3u_tie_break_enabled = True
        settings = MagicMock()
        settings.deprioritize_failed_streams = False
        settings.m3u_account_priorities = {"1": 10, "2": 5}
        stats_cache = {
            1: {"resolution": "1920x1080", "probe_status": "success"},
            2: {"resolution": "1920x1080", "probe_status": "success"},
        }
        stream_m3u_map = {1: 2, 2: 1}
        out = _reorder_streams_for_rule(
            [1, 2], rule, stats_cache, stream_m3u_map, "Ch", settings
        )
        assert out == [1, 2]


class TestSortKey:
    """Tests for _sort_key with provider_order and channel_number."""

    def test_provider_order_returns_m3u_position(self):
        """provider_order sort returns m3u_position."""
        stream = StreamContext(stream_id=1, stream_name="ESPN", m3u_position=42)
        assert _sort_key(stream, "provider_order") == 42

    def test_channel_number_returns_stream_chno(self):
        """channel_number sort returns stream_chno."""
        stream = StreamContext(stream_id=1, stream_name="ESPN", stream_chno=21262.0)
        assert _sort_key(stream, "channel_number") == 21262.0

    def test_channel_number_none_returns_infinity(self):
        """channel_number sort returns infinity when stream_chno is None."""
        stream = StreamContext(stream_id=1, stream_name="ESPN", stream_chno=None)
        assert _sort_key(stream, "channel_number") == float('inf')


def _mk_smart_sort_settings(
    stream_sort_priority=None,
    stream_sort_enabled=None,
    m3u_account_priorities=None,
    deprioritize_failed_streams=True,
    failed_stream_sort_order=None,
):
    """Build a MagicMock-backed settings object for _smart_sort_streams.

    Mirrors the DispatcharrSettings attribute surface that
    ``auto_creation_engine._smart_sort_streams`` reads via ``getattr``.
    """
    settings = MagicMock()
    settings.stream_sort_priority = stream_sort_priority or [
        "resolution", "framerate", "m3u_priority", "bitrate", "audio_channels", "video_codec",
    ]
    settings.stream_sort_enabled = stream_sort_enabled or {
        "resolution": True, "framerate": True, "m3u_priority": True,
        "bitrate": True, "audio_channels": True, "video_codec": True,
    }
    settings.m3u_account_priorities = m3u_account_priorities or {}
    settings.deprioritize_failed_streams = deprioritize_failed_streams
    settings.failed_stream_sort_order = failed_stream_sort_order or [
        "black_screen", "low_fps", "failed",
    ]
    return settings


def _bs_stats_dict(stream_id, resolution, fps, stream_name=None):
    """Build a stats-dict for a black-screen success-probed stream (rank=0 under
    failed_stream_sort_order=['black_screen', 'low_fps', 'failed'])."""
    return {
        "stream_id": stream_id,
        "stream_name": stream_name or f"Stream {stream_id}",
        "resolution": resolution,
        "fps": fps,
        "video_codec": "h264",
        "audio_codec": "aac",
        "audio_channels": 2,
        "bitrate": 5_000_000,
        "video_bitrate": 5_000_000,
        "probe_status": "success",
        "is_black_screen": True,
        "is_low_fps": False,
    }


def _failed_stats_dict(stream_id, resolution, fps, stream_name=None):
    """Build a stats-dict for a status=failed stream (rank=2 under
    failed_stream_sort_order=['black_screen', 'low_fps', 'failed'])."""
    return {
        "stream_id": stream_id,
        "stream_name": stream_name or f"Stream {stream_id}",
        "resolution": resolution,
        "fps": fps,
        "video_codec": "h264",
        "audio_codec": "aac",
        "audio_channels": 2,
        "bitrate": 5_000_000,
        "video_bitrate": 5_000_000,
        "probe_status": "failed",
        "is_black_screen": False,
        "is_low_fps": False,
    }


class TestAutoCreateSmartSortWithinBucketPrimaryCriteria:
    """Regression tests for bd-bqpq0 (same pattern as bd-sw883 / GitHub #73)
    applied to ``auto_creation_engine._smart_sort_streams``.

    Primary sort criteria (resolution, framerate, ...) must be applied WITHIN
    each failed-rank bucket — not just at the bucket-boundary level. Previously
    the composite sort key for deprioritized streams was
    ``(1, rank) + (0,)*len(active_criteria)`` so every stream inside a bucket
    collided on the key and Python's stable sort kept insertion order.
    """

    def test_black_screen_bucket_sorted_by_resolution_desc(self):
        """Within the black_screen bucket, higher resolution sorts first."""
        settings = _mk_smart_sort_settings()
        stats_cache = {
            1: _bs_stats_dict(1, resolution="1024x576", fps="25"),
            2: _bs_stats_dict(2, resolution="1920x1080", fps="25"),
            3: _bs_stats_dict(3, resolution="1024x576", fps="25"),
        }
        result = _smart_sort_streams(
            [1, 2, 3],
            stats_cache,
            stream_m3u_map={},
            channel_name="bqpq0-bs-bucket",
            settings=settings,
        )
        assert result[0] == 2, (
            f"Expected 1920x1080 stream (id=2) at #1 in black_screen bucket, "
            f"got ordering {result}"
        )

    def test_failed_bucket_sorted_by_resolution_then_framerate(self):
        """Within the status=failed bucket, resolution desc then framerate desc apply.

        Reporter's exact scenario from issue #73: 1280x720@25 was landing ahead
        of 1920x1080@50 inside the failed bucket because the within-bucket
        tiebreaker was a tuple of zeros across all primary criteria.
        """
        settings = _mk_smart_sort_settings()
        stats_cache = {
            10: _failed_stats_dict(10, resolution="1280x720", fps="25"),
            20: _failed_stats_dict(20, resolution="1920x1080", fps="50"),
            30: _failed_stats_dict(30, resolution="1280x720", fps="25"),
            40: _failed_stats_dict(40, resolution="1920x1080", fps="50"),
        }
        result = _smart_sort_streams(
            [10, 20, 30, 40],
            stats_cache,
            stream_m3u_map={},
            channel_name="bqpq0-failed-bucket",
            settings=settings,
        )
        # Expected: 1920x1080@50 streams (20, 40) lead the bucket, then the
        # 1280x720@25 streams (10, 30). Python's stable sort preserves
        # insertion order within each equal-key group.
        assert result[:2] == [20, 40], (
            f"Expected 1920x1080@50 streams (20, 40) to lead the failed bucket, "
            f"got {result}"
        )
        assert result[2:] == [10, 30], (
            f"Expected 1280x720@25 streams (10, 30) at the tail of the failed bucket, "
            f"got {result}"
        )

    def test_cross_bucket_ordering_preserved(self):
        """Cross-bucket invariant must not regress: rank=0 (black_screen) still
        precedes rank=2 (failed) even when primary criteria would invert it.
        """
        settings = _mk_smart_sort_settings()
        stats_cache = {
            # rank=0 (black_screen) but lower-resolution content
            1: _bs_stats_dict(1, resolution="1024x576", fps="25"),
            # rank=2 (failed) but higher-resolution content
            2: _failed_stats_dict(2, resolution="1920x1080", fps="50"),
        }
        result = _smart_sort_streams(
            [1, 2],
            stats_cache,
            stream_m3u_map={},
            channel_name="bqpq0-cross-bucket",
            settings=settings,
        )
        assert result == [1, 2], (
            f"Cross-bucket invariant broken: expected rank=0 before rank=2, "
            f"got {result}"
        )

    def test_ferteque_channel_scenario(self):
        """Condensed reproduction of reporter's channel-591424 case.

        3 black_screen streams (rank=0) and 3 failed streams (rank=2). Expected:
        - Black_screen bucket leads.
        - Within black_screen, the 1920x1080 stream leads (was landing #2 in prod).
        - Within failed, the 1920x1080@50 stream leads (was landing #8 in prod).
        """
        settings = _mk_smart_sort_settings()
        stats_cache = {
            # Black-screen bucket — from reporter's log
            101: _bs_stats_dict(101, resolution="1024x576", fps="25"),   # D.LaLiga2
            102: _bs_stats_dict(102, resolution="1920x1080", fps="25"),  # UHD
            103: _bs_stats_dict(103, resolution="1024x576", fps="25"),   # SD
            # Failed bucket — condensed
            201: _failed_stats_dict(201, resolution="1280x720", fps="25"),   # HD
            202: _failed_stats_dict(202, resolution="1920x1080", fps="50"),  # HD 1080
            203: _failed_stats_dict(203, resolution="1920x1080", fps="25"),  # S.LaLiga2
        }
        result = _smart_sort_streams(
            [101, 102, 103, 201, 202, 203],
            stats_cache,
            stream_m3u_map={},
            channel_name="bqpq0-ferteque",
            settings=settings,
        )
        bs_bucket = result[:3]
        failed_bucket = result[3:]
        # Within black_screen bucket — 1920x1080 (id=102) leads
        assert bs_bucket[0] == 102, (
            f"Expected 1920x1080 black_screen stream (102) to lead bucket, "
            f"got black_screen bucket order {bs_bucket}"
        )
        # Within failed bucket — 1920x1080@50 (id=202) leads,
        # then 1920x1080@25 (id=203), then 1280x720@25 (id=201)
        assert failed_bucket == [202, 203, 201], (
            f"Expected failed bucket ordered by resolution desc then framerate desc "
            f"— [202, 203, 201] — got {failed_bucket}"
        )
