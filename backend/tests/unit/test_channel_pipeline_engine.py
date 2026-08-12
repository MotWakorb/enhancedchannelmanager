"""
Unit tests for the auto-creation engine service.

Tests the ChannelPipelineEngine class which orchestrates the entire auto-creation
pipeline, coordinating rules, streams, and executions.
"""
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timedelta
import asyncio
import json
import pytest

from channel_pipeline_engine import (
    ChannelPipelineEngine,
    get_channel_pipeline_engine,
    set_channel_pipeline_engine,
    init_channel_pipeline_engine,
    _sort_key,
    _smart_sort_streams,
    _sort_streams_by_m3u_account_priority,
    _sort_streams_by_resolution_height,
    _reorder_streams_for_rule,
)
from channel_pipeline_evaluator import StreamContext
from channel_pipeline_evaluator import StreamContext
from channel_pipeline_executor import ActionExecutor, ExecutionContext


class TestChannelPipelineEngineInit:
    """Tests for ChannelPipelineEngine initialization."""

    def test_init(self):
        """Initialize engine with client."""
        client = MagicMock()
        engine = ChannelPipelineEngine(client)

        assert engine.client == client
        assert engine._existing_channels is None
        assert engine._existing_groups is None
        assert engine._stream_stats_cache == {}


class TestChannelPipelineEngineSingleton:
    """Tests for singleton pattern helpers."""

    def test_get_engine_default_none(self):
        """get_channel_pipeline_engine returns None by default."""
        # Reset global
        set_channel_pipeline_engine(None)
        assert get_channel_pipeline_engine() is None

    def test_set_and_get_engine(self):
        """set_channel_pipeline_engine and get work together."""
        client = MagicMock()
        engine = ChannelPipelineEngine(client)

        set_channel_pipeline_engine(engine)
        result = get_channel_pipeline_engine()

        assert result is engine

    def test_init_channel_pipeline_engine(self):
        """init_channel_pipeline_engine creates and sets engine."""
        client = MagicMock()

        result = asyncio.get_event_loop().run_until_complete(
            init_channel_pipeline_engine(client)
        )

        assert result is not None
        assert get_channel_pipeline_engine() is result


class TestChannelPipelineEngineLoadData:
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
        self.engine = ChannelPipelineEngine(self.client)

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


class TestChannelPipelineEngineLoadRules:
    """Tests for rule loading methods — real in-memory session with seeded rows.

    The original tests mocked the SQLAlchemy session with inert MagicMock chains
    and only asserted call-presence. They passed even if _load_rules returned an
    empty list or ignored the filter. These tests use the real ORM with seeded rows
    to verify what is actually loaded and excluded.
    """

    def setup_method(self):
        """Set up test fixtures."""
        self.client = MagicMock()
        self.engine = ChannelPipelineEngine(self.client)

    def _make_rule(self, name: str, priority: int, enabled: bool = True, **kwargs):
        """Create a minimal ChannelPipelineRule for seeding."""
        from models import ChannelPipelineRule
        import json
        return ChannelPipelineRule(
            name=name,
            enabled=enabled,
            priority=priority,
            conditions=json.dumps([]),
            actions=json.dumps([]),
            **kwargs,
        )

    def test_load_rules_only_inside_inclusive_active_window(self, test_session):
        today = datetime.utcnow().date()
        rules = [
            self._make_rule("No window", 0),
            self._make_rule("Starts today", 1, active_from=today),
            self._make_rule("Ends today", 2, active_until=today),
            self._make_rule("Future", 3, active_from=today + timedelta(days=1)),
            self._make_rule("Expired", 4, active_until=today - timedelta(days=1)),
        ]
        test_session.add_all(rules)
        test_session.commit()

        with patch("channel_pipeline_engine.get_session", return_value=test_session):
            loaded = asyncio.get_event_loop().run_until_complete(
                self.engine._load_rules()
            )

        assert [rule.name for rule in loaded] == [
            "No window", "Starts today", "Ends today"
        ]

    def test_load_rules_all_enabled(self, test_session):
        """Loads only enabled rules, sorted by priority ascending.

        Mutation guard: if the enabled=True filter were removed, rule3 (disabled) would
        appear. If sorting were removed, order would not be guaranteed and rules[0].priority
        might not be 0.
        """
        rule1 = self._make_rule("Rule A", priority=0, enabled=True)
        rule2 = self._make_rule("Rule B", priority=1, enabled=True)
        rule3 = self._make_rule("Rule Disabled", priority=-1, enabled=False)
        test_session.add_all([rule1, rule2, rule3])
        test_session.commit()

        with patch("channel_pipeline_engine.get_session", return_value=test_session):
            rules = asyncio.get_event_loop().run_until_complete(
                self.engine._load_rules()
            )

        assert len(rules) == 2
        # Should be sorted by priority ascending (lower = higher priority = first)
        assert rules[0].name == "Rule A"
        assert rules[1].name == "Rule B"
        # Disabled rule must NOT appear
        assert all(r.enabled for r in rules)

    def test_load_rules_specific_ids(self, test_session):
        """Loads only the specified rule IDs, skipping others even if enabled.

        Mutation guard: if the id.in_() filter were removed, both enabled rules
        would be returned instead of just the requested one.
        """
        rule1 = self._make_rule("Rule A", priority=0, enabled=True)
        rule2 = self._make_rule("Rule B", priority=1, enabled=True)
        test_session.add_all([rule1, rule2])
        test_session.commit()

        with patch("channel_pipeline_engine.get_session", return_value=test_session):
            rules = asyncio.get_event_loop().run_until_complete(
                self.engine._load_rules(rule_ids=[rule1.id])
            )

        assert len(rules) == 1
        assert rules[0].id == rule1.id
        assert rules[0].name == "Rule A"


class TestChannelPipelineEngineFetchStreams:
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
        self.engine = ChannelPipelineEngine(self.client)
        # Pre-populate existing groups so _fetch_streams doesn't need them unset
        self.engine._existing_groups = []

    @patch("channel_pipeline_engine.get_session")
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

    @patch("channel_pipeline_engine.get_session")
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

    @patch("channel_pipeline_engine.get_session")
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

    @patch("channel_pipeline_engine.get_session")
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


class TestChannelPipelineEngineRunPipeline:
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
        self.engine = ChannelPipelineEngine(self.client)

    @patch("channel_pipeline_engine.get_session")
    def test_run_pipeline_no_rules(self, mock_get_session):
        """Run pipeline with no enabled rules."""
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        result = asyncio.get_event_loop().run_until_complete(
            self.engine.run_pipeline()
        )

        assert result["success"] is True
        assert result["message"] == "No active enabled rules to process"
        assert result["streams_evaluated"] == 0

    def test_selected_expired_rule_is_noop_without_undo_or_writes(self, test_session):
        """Expiry gates execution; it never reverses effects from prior runs."""
        from models import ChannelPipelineRule

        rule = ChannelPipelineRule(
            name="Expired selected rule", enabled=True, priority=0,
            active_until=datetime.utcnow().date() - timedelta(days=1),
            conditions=json.dumps([{"type": "always"}]),
            actions=json.dumps([{"type": "create_channel", "name_template": "x"}]),
        )
        test_session.add(rule)
        test_session.commit()
        with patch("channel_pipeline_engine.get_session", return_value=test_session):
            result = asyncio.get_event_loop().run_until_complete(
                self.engine.run_pipeline(rule_ids=[rule.id], triggered_by="manual")
            )

        assert result["message"] == "No active enabled rules to process"
        self.client.create_channel.assert_not_awaited()
        for method_name in ("update_channel", "delete_channel"):
            method = getattr(self.client, method_name, None)
            if method is not None:
                method.assert_not_called()

    @patch("channel_pipeline_engine.get_session")
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
        # ti939.1.3: a MagicMock's is_event_sync() is truthy by default,
        # which would wrongly classify this standard rule as event_sync and
        # exclude it from the run.
        mock_rule.is_event_sync.return_value = False

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

    @patch("channel_pipeline_engine.get_session")
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
        assert result["message"] == "No active enabled rules to process"


def _route_rollback_queries(mock_session, mock_execution):
    """Wire a MagicMock session so the LEGACY (no-snapshot) rollback path runs.

    uc51o.5 made ``rollback_execution`` first probe for an ChannelPipelineSnapshot
    and divert to the full restore when one exists. A plain
    ``mock_session.query.return_value...`` returns the SAME chain for every
    query, so the snapshot-existence probe would wrongly see the execution mock
    and treat the run as snapshotted. These legacy tests model NO-snapshot runs,
    so route the ChannelPipelineSnapshot probe to None and everything else to the
    execution.
    """
    from models import ChannelPipelineSnapshot

    exec_chain = MagicMock()
    exec_chain.filter.return_value.first.return_value = mock_execution
    none_chain = MagicMock()
    none_chain.filter.return_value.first.return_value = None

    def _is_snapshot_arg(a):
        # The probe is session.query(ChannelPipelineSnapshot.id) — a column
        # attribute whose owning class is ChannelPipelineSnapshot. Also match the
        # class itself for robustness.
        return a is ChannelPipelineSnapshot or getattr(a, "class_", None) is ChannelPipelineSnapshot

    def _query(*args, **kwargs):
        if any(_is_snapshot_arg(a) for a in args):
            return none_chain
        return exec_chain

    mock_session.query.side_effect = _query


class TestChannelPipelineEngineRollback:
    """Tests for rollback functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.client = MagicMock()
        self.client.delete_channel = AsyncMock()
        self.client.delete_channel_group = AsyncMock()
        self.client.update_channel = AsyncMock()
        self.engine = ChannelPipelineEngine(self.client)

    @patch("channel_pipeline_engine.get_session")
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

    @patch("channel_pipeline_engine.get_session")
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

    @patch("channel_pipeline_engine.get_session")
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

    @patch("channel_pipeline_engine.get_session")
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
        _route_rollback_queries(mock_session, mock_execution)

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

    @patch("channel_pipeline_engine.get_session")
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
        _route_rollback_queries(mock_session, mock_execution)

        # Make delete fail
        self.client.delete_channel = AsyncMock(side_effect=Exception("API error"))

        result = asyncio.get_event_loop().run_until_complete(
            self.engine.rollback_execution(1)
        )

        # Should still succeed (errors are logged but don't fail rollback)
        assert result["success"] is True

        # The delete was attempted — the API-error swallow path was actually hit.
        self.client.delete_channel.assert_called_once_with(1)

    @patch("channel_pipeline_engine.get_session")
    def test_rollback_unmerge_multiple_into_same_channel_restores_original(self, mock_get_session):
        """bd-a7okb: multiple merges into ONE pre-existing channel restore to the
        true original stream list, not the second-to-last snapshot.

        A run that merges streams 11, 12, 13 into a channel that already held
        [10] records cumulative `previous` snapshots, one per merge:
            before 11 -> [10]
            before 12 -> [10, 11]
            before 13 -> [10, 11, 12]
        Restore is overwrite/last-write-wins. Applied FORWARD, the final state
        would be [10, 11, 12] (only the last merge removed). Applied REVERSED,
        the earliest snapshot [10] wins — the correct original. This asserts the
        LAST update_channel call restores [10].
        """
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session

        mock_execution = MagicMock()
        mock_execution.status = "completed"
        mock_execution.mode = "execute"
        mock_execution.get_created_entities.return_value = []
        mock_execution.get_modified_entities.return_value = [
            {"type": "channel", "id": 3, "name": "ESPN", "previous": {"streams": [10]}},
            {"type": "channel", "id": 3, "name": "ESPN", "previous": {"streams": [10, 11]}},
            {"type": "channel", "id": 3, "name": "ESPN", "previous": {"streams": [10, 11, 12]}},
        ]
        _route_rollback_queries(mock_session, mock_execution)

        result = asyncio.get_event_loop().run_until_complete(
            self.engine.rollback_execution(1)
        )

        assert result["success"] is True
        assert result["entities_restored"] == 3
        assert self.client.update_channel.call_count == 3
        # The decisive assertion: the final write must restore the ORIGINAL list.
        last_call = self.client.update_channel.call_args_list[-1]
        assert last_call.args == (3, {"streams": [10]}), (
            "rollback must end by restoring the pre-run streams [10]; "
            f"last update_channel was {last_call.args} (forward-order bug leaves extra streams)"
        )


class TestChannelPipelineEngineProcessStreams:
    """Tests for stream processing logic."""

    def setup_method(self):
        """Set up test fixtures."""
        self.client = MagicMock()
        self.client.create_channel = AsyncMock(return_value={"id": 1, "name": "Test"})
        self.client.update_channel = AsyncMock()
        self.client.create_channel_group = AsyncMock(return_value={"id": 1, "name": "Test"})
        self.engine = ChannelPipelineEngine(self.client)
        self.engine._existing_channels = []
        self.engine._existing_groups = []

    @patch("channel_pipeline_engine.get_session")
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

    @patch("channel_pipeline_engine.get_session")
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

        with patch("channel_pipeline_engine.get_session") as mock_get_session:
            mock_session = MagicMock()
            mock_get_session.return_value = mock_session

            result = asyncio.get_event_loop().run_until_complete(
                self.engine._process_streams(streams, [mock_rule1, mock_rule2], mock_execution, dry_run=True)
            )

        # Should detect conflict
        assert len(result["conflicts"]) == 1
        assert result["conflicts"][0]["winning_rule_id"] == 1
        assert result["conflicts"][0]["losing_rule_ids"] == [2]

    @patch("channel_pipeline_engine.get_session")
    def test_process_streams_stop_processing(self, mock_get_session):
        """A stop_processing action does NOT halt Pass 2 for other streams.

        Regression for bd-iqm50 / GH #225: the old ``if stop_processing: break``
        at the end of the Pass 2 per-stream block exited the *entire*
        sorted_entries loop, so the first stream whose winning rule had a
        stop_processing action ended Pass 2 for all remaining streams. Pass 1
        already resolves one winning rule per stream, so STOP_PROCESSING has no
        remaining rules to stop here — it must be a no-op at the per-stream
        level and all matched streams must still be processed.
        """
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

        # Both streams are evaluated in Pass 1 AND both actioned in Pass 2.
        assert result["streams_evaluated"] == 2
        assert result["streams_matched"] == 2

    @patch("channel_pipeline_engine.get_session")
    def test_process_streams_stop_processing_does_not_truncate_large_batch(self, mock_get_session):
        """bd-iqm50 / GH #225: with many matching streams + a stop_processing
        rule, ALL streams are processed in Pass 2 (not just the first one)."""
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session

        streams = [
            StreamContext(stream_id=i, stream_name="ESPN %d" % i, m3u_account_id=1,
                          m3u_account_name="Provider")
            for i in range(1, 13)  # 12 matching streams
        ]

        mock_rule = MagicMock()
        mock_rule.id = 1
        mock_rule.name = "Stop Rule"
        mock_rule.priority = 0
        mock_rule.m3u_account_id = None
        mock_rule.target_group_id = None
        mock_rule.sort_field = None  # don't trigger the between-passes sort
        mock_rule.get_conditions.return_value = [{"type": "always"}]
        mock_rule.get_actions.return_value = [{"type": "stop_processing"}]
        mock_rule.stop_on_first_match = True

        mock_execution = MagicMock()
        mock_execution.id = 1

        result = asyncio.get_event_loop().run_until_complete(
            self.engine._process_streams(streams, [mock_rule], mock_execution, dry_run=True)
        )

        assert result["streams_evaluated"] == 12
        assert result["streams_matched"] == 12


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
        from channel_pipeline_executor import ActionResult, ExecutionContext

        self.ActionResult = ActionResult
        self.ExecutionContext = ExecutionContext

        self.client = MagicMock()
        self.client.assign_channel_numbers = AsyncMock()
        self.client.get_channels = AsyncMock(return_value={"count": 0, "results": []})
        self.engine = ChannelPipelineEngine(self.client)
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
                                match_scope_target_group=False,
                                rule_scope_group_id=None,
                                allow_manual_channel_merge=False,
                                fold_match_key=False,
                                rule_id=None):
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

    @patch("channel_pipeline_engine.get_session")
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

        with patch("channel_pipeline_engine.ActionExecutor") as mock_exec_cls:
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

    @patch("channel_pipeline_engine.get_session")
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
                                match_scope_target_group=False,
                                rule_scope_group_id=None,
                                allow_manual_channel_merge=False,
                                fold_match_key=False,
                                rule_id=None):
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

        with patch("channel_pipeline_engine.ActionExecutor") as mock_exec_cls, \
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

    @patch("channel_pipeline_engine._auto_rename_after_renumber", new_callable=AsyncMock)
    @patch("channel_pipeline_engine.get_session")
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

        with patch("channel_pipeline_engine.ActionExecutor") as mock_exec_cls:
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

    @patch("channel_pipeline_engine._auto_rename_after_renumber", new_callable=AsyncMock)
    @patch("channel_pipeline_engine.get_session")
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

        with patch("channel_pipeline_engine.ActionExecutor") as mock_exec_cls:
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


class TestPass35SkippedMergeRegistration:
    """Pin the skipped-merge -> Pass 3.5 registration side effect (bead io0tv).

    A merge_stream result with skipped=True (stream already on the channel)
    still lands in actions_log with type "merge_stream" and entity_id, so the
    modified_this_run gate registers the channel in rule_channel_order_streams
    — meaning a stream_sort_field rule re-heals ordering even on no-op re-runs.
    The QA spike flagged this as load-bearing (companion-rule recipes depend on
    it) but previously unpinned; this test keeps future cleanups from silently
    killing it.
    """

    def setup_method(self):
        from channel_pipeline_executor import ActionResult

        self.ActionResult = ActionResult
        self.client = MagicMock()
        self.client.assign_channel_numbers = AsyncMock()
        self.client.get_channels = AsyncMock(return_value={"count": 0, "results": []})
        self.engine = ChannelPipelineEngine(self.client)
        self.engine._existing_channels = []
        self.engine._existing_groups = []

    @patch("channel_pipeline_engine.get_session")
    def test_skipped_merge_still_registers_channel_for_reorder(self, mock_get_session):
        mock_get_session.return_value = MagicMock()

        streams = [
            StreamContext(stream_id=201, stream_name="A", m3u_account_id=1, m3u_account_name="P"),
        ]

        rule = MagicMock()
        rule.id = 3
        rule.name = "Rule"
        rule.priority = 0
        rule.m3u_account_id = None
        rule.target_group_id = None
        rule.enabled = True
        rule.stop_on_first_match = True
        rule.skip_struck_streams = False
        rule.sort_field = None
        rule.sort_order = "asc"
        rule.sort_regex = None
        rule.starting_channel_number = None
        rule.orphan_action = "none"
        rule.managed_channel_ids = None
        rule.get_managed_channel_ids.return_value = []
        rule.get_conditions.return_value = [{"type": "always"}]
        rule.get_actions.return_value = [{"type": "create_channel", "params": {}}]
        rule.get_normalization_group_ids.return_value = []
        rule.match_scope_target_group = False
        rule.stream_sort_field = "provider_order"
        rule.stream_sort_order = "desc"

        # Idempotent no-op merge: the stream is ALREADY on channel 501, so the
        # executor returns skipped=True and modified=False.
        async def _fake_execute(action, stream_ctx, exec_ctx,
                                rule_target_group_id=None,
                                normalization_group_ids=None,
                                match_scope_target_group=False,
                                rule_scope_group_id=None,
                                allow_manual_channel_merge=False,
                                fold_match_key=False,
                                rule_id=None):
            exec_ctx.current_channel_id = 501
            return self.ActionResult(
                success=True,
                action_type="merge_stream",
                description="Stream already in channel 'ch-501' (2 streams)",
                entity_type="channel",
                entity_id=501,
                entity_name="ch-501",
                skipped=True,
            )

        mock_execution = MagicMock()
        mock_execution.id = 1

        with patch("channel_pipeline_engine.ActionExecutor") as mock_exec_cls, \
             patch.object(self.engine, "_reorder_channel_streams", new_callable=AsyncMock) as mock_reorder:
            mock_executor = MagicMock()
            mock_executor.execute = AsyncMock(side_effect=_fake_execute)
            mock_executor.verify_epg_assignments = AsyncMock(return_value=(0, 0, 0))
            mock_executor.prune_merge_streams = AsyncMock()
            mock_executor._channel_by_id = {}
            mock_executor._created_channels = {}
            mock_exec_cls.return_value = mock_executor

            self.engine._refresh_dummy_epg_and_retry = AsyncMock()
            self.engine._reconcile_orphans = AsyncMock()
            self.engine._update_rule_stats = AsyncMock()

            asyncio.get_event_loop().run_until_complete(
                self.engine._process_streams(streams, [rule], mock_execution, dry_run=False)
            )

        assert mock_reorder.await_count == 1
        passed_rule_channel_order = mock_reorder.await_args.args[1]
        assert passed_rule_channel_order.get(rule.id) == [501], (
            "skipped merge no longer registers the channel for Pass 3.5 — "
            "stream_sort_field rules would stop healing order on no-op re-runs"
        )


class TestEventSyncStreamReorderWiring:
    """Pass 3.5 wiring for event_sync rules (bead io0tv).

    The attach phase registers touched master channels in
    rule_channel_order_streams and the Pass 3.5 call receives the event_sync
    rules alongside the standard rules, so an event_sync rule's
    stream_sort_field orders streams within its master channels.
    """

    EVENT_SYNC_CONFIG = {
        "master_group_id": 10,
        "secondary_group_ids": [20],
        "time_window_minutes": 30,
        "attach_threshold": 0.80,
        "enabled": True,
    }

    def setup_method(self):
        self.client = MagicMock()
        self.client.update_channel = AsyncMock(return_value={})
        self.client.get_channels = AsyncMock(return_value={"count": 0, "results": []})
        self.client.get_m3u_accounts = AsyncMock(return_value=[])
        self.client.get_all_m3u_group_settings = AsyncMock(return_value={})
        self.client.get_streams_by_ids = AsyncMock(return_value=[])
        self.engine = ChannelPipelineEngine(self.client)
        self.engine._existing_channels = []
        self.engine._existing_groups = []

    def _make_event_rule(self, rule_id=9, stream_sort_field="provider_order"):
        rule = MagicMock()
        rule.id = rule_id
        rule.name = "Event Rule"
        rule.priority = 0
        rule.enabled = True
        rule.get_event_sync_config.return_value = dict(self.EVENT_SYNC_CONFIG)
        rule.is_event_sync.return_value = True
        rule.stream_sort_field = stream_sort_field
        rule.stream_sort_order = "desc"
        rule.get_actions.return_value = []
        rule.get_normalization_group_ids.return_value = []
        rule.get_conditions.return_value = []
        return rule

    def _run_process_streams(self, event_rule, mock_reorder_patch=True,
                             merged_ids=(501,), attach_entries=None):
        """Drive _process_streams with only an event_sync rule; the executor's
        execute_event_sync_rule is mocked to simulate attaches into master
        channels (exec_ctx.merged_channel_ids)."""
        summary_template = {
            "rule_id": event_rule.id,
            "rule_name": event_rule.name,
            "master_group_id": 10,
            "secondary_group_ids": [20],
            "secondary_streams": 1,
            "master_channels": 1,
            "master_channels_unparsed": 0,
            "attached": len(merged_ids),
            "already_attached": 0,
            "ambiguous_skipped": 0,
            "contested_skipped": 0,
            "unmatched": 0,
            "parse_failed": 0,
            "attach_errors": 0,
            "cap": 100,
            "capped": False,
            "cap_overage": 0,
            "attach_entries": list(attach_entries or []),
            "queue_attached": 0,
            "rejected_suppressed": 0,
            "review_candidates": [],
        }

        async def _fake_event_sync(rule_id, rule_name, config,
                                   secondary_streams, exec_ctx,
                                   decisions=None,
                                   effective_master_group_id=None,
                                   exclusions=None):
            for cid in merged_ids:
                exec_ctx.merged_channel_ids.add(cid)
            return dict(summary_template)

        mock_execution = MagicMock()
        mock_execution.id = 1

        patches = [
            patch("channel_pipeline_engine.ActionExecutor"),
            patch("channel_pipeline_engine.get_session", return_value=MagicMock()),
            patch.object(self.engine, "_fetch_event_sync_secondary_streams",
                         new=AsyncMock(return_value=[])),
        ]
        if mock_reorder_patch:
            reorder_patch = patch.object(
                self.engine, "_reorder_channel_streams", new_callable=AsyncMock
            )
        else:
            reorder_patch = None

        with patches[0] as mock_exec_cls, patches[1], patches[2]:
            mock_executor = MagicMock()
            mock_executor.execute_event_sync_rule = AsyncMock(
                side_effect=_fake_event_sync
            )
            mock_executor.verify_epg_assignments = AsyncMock(return_value=(0, 0, 0))
            mock_executor.prune_merge_streams = AsyncMock()
            mock_executor._channel_by_id = {}
            mock_executor._created_channels = {}
            mock_exec_cls.return_value = mock_executor

            self.engine._refresh_dummy_epg_and_retry = AsyncMock()
            self.engine._reconcile_orphans = AsyncMock()
            self.engine._update_rule_stats = AsyncMock()

            if reorder_patch is not None:
                with reorder_patch as mock_reorder:
                    asyncio.get_event_loop().run_until_complete(
                        self.engine._process_streams(
                            [], [], mock_execution, dry_run=False,
                            triggered_by="manual",
                            event_sync_rules=[event_rule],
                        )
                    )
                    return mock_reorder
            asyncio.get_event_loop().run_until_complete(
                self.engine._process_streams(
                    [], [], mock_execution, dry_run=False,
                    triggered_by="manual",
                    event_sync_rules=[event_rule],
                )
            )
            return None

    def test_event_sync_attach_registers_masters_for_reorder(self):
        """Mirror of test_stream_reorder_runs_on_modified_existing_channel for
        the event_sync path: an attach into master 501 must hand
        {event_rule.id: [501]} to Pass 3.5, with the event rule in the rules
        list Pass 3.5 receives."""
        event_rule = self._make_event_rule(stream_sort_field="provider_order")

        mock_reorder = self._run_process_streams(event_rule, merged_ids=(501,))

        assert mock_reorder.await_count == 1
        passed_rules = mock_reorder.await_args.args[0]
        assert event_rule in passed_rules, (
            "event_sync rule missing from Pass 3.5 rules list — its "
            "stream_sort_field can never apply"
        )
        passed_rule_channel_order = mock_reorder.await_args.args[1]
        assert passed_rule_channel_order.get(event_rule.id) == [501]

    def test_already_attached_no_op_run_still_registers_for_reorder(self):
        """Idempotent re-run: zero new attaches, one already-attached skip.
        The master must STILL be registered so ordering heals on every run
        (e.g. after the operator changes m3u_account_priorities)."""
        event_rule = self._make_event_rule(stream_sort_field="provider_order")
        skip_entry = {
            "type": "event_sync_attach",
            "description": "Stream already in channel 'master' (2 streams)",
            "success": True,
            "skipped": True,
            "entity_id": 501,
            "entity_name": "master",
            "error": None,
            "match": {},
        }

        mock_reorder = self._run_process_streams(
            event_rule, merged_ids=(), attach_entries=[skip_entry]
        )

        assert mock_reorder.await_count == 1
        passed_rule_channel_order = mock_reorder.await_args.args[1]
        assert passed_rule_channel_order.get(event_rule.id) == [501], (
            "already-attached no-op run no longer registers the master for "
            "Pass 3.5 — ordering would stop healing on steady-state runs"
        )

    def test_event_sync_rule_without_sort_field_never_reorders(self):
        """No stream_sort_field -> Pass 3.5 no-ops the rule: no update_channel
        call for the master even though it was registered (no behavior change
        for existing event_sync rules)."""
        event_rule = self._make_event_rule(stream_sort_field=None)
        # Real _reorder_channel_streams runs; give it a master with 2 streams
        # so a reorder WOULD be possible if the gate failed.
        self.engine._existing_channels = [
            {"id": 501, "name": "master", "streams": [9001, 7001]},
        ]

        self._run_process_streams(
            event_rule, mock_reorder_patch=False, merged_ids=(501,)
        )

        self.client.update_channel.assert_not_awaited()


class TestChannelPipelineEngineStreamReorderLogging:
    """Tests for Pass 3.5 stream reorder logging."""

    def setup_method(self):
        self.client = MagicMock()
        self.client.update_channel = AsyncMock()
        self.engine = ChannelPipelineEngine(self.client)

    @patch("channel_pipeline_engine._reorder_streams_for_rule")
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


class TestChannelPipelineEngineStreamReorderUsesChannelNames:
    """Regression: name-based stream sorting should work without probe stats rows."""

    def setup_method(self):
        self.client = MagicMock()
        self.client.update_channel = AsyncMock()
        self.engine = ChannelPipelineEngine(self.client)
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


class TestSortChannelGroupsPass:
    """Tests for Pass 3.6 — the sort_group post-run pass
    (enhancedchannelmanager-vy4fl)."""

    def setup_method(self):
        self.client = MagicMock()
        self.client.assign_channel_numbers = AsyncMock(return_value={})
        self.client.get_channel = AsyncMock()
        self.client.update_channel = AsyncMock()
        self.engine = ChannelPipelineEngine(self.client)
        self.channels = [
            {"id": 1, "name": "Channel 10", "channel_group_id": 5, "channel_number": 10},
            {"id": 2, "name": "Channel 2", "channel_group_id": 5, "channel_number": 20},
            {"id": 3, "name": "Other Group Channel", "channel_group_id": 6, "channel_number": 1},
        ]
        self.groups = [{"id": 5, "name": "Sports"}, {"id": 6, "name": "News"}]
        self.executor = ActionExecutor(
            self.client, existing_channels=self.channels, existing_groups=self.groups,
        )
        # Real settings would come from get_settings(); a bare object with
        # the one attribute _auto_rename_after_renumber reads is enough —
        # avoids MagicMock's truthy-by-default auto-rename path firing.
        self.settings = MagicMock()
        self.settings.auto_rename_channel_number = False

    def test_no_requests_is_a_noop(self):
        results = {"execution_log": [], "dry_run_results": []}
        asyncio.get_event_loop().run_until_complete(
            self.engine._sort_channel_groups({}, self.executor, results, dry_run=False, settings=self.settings)
        )
        assert results["execution_log"] == []
        self.client.assign_channel_numbers.assert_not_called()

    def test_single_channel_group_is_skipped(self):
        """A group with fewer than 2 channels has nothing to sort."""
        results = {"execution_log": [], "dry_run_results": []}
        requests = {6: {"order": "asc", "starting_number": None, "strip_numbers": True, "ignore_country": False}}
        asyncio.get_event_loop().run_until_complete(
            self.engine._sort_channel_groups(requests, self.executor, results, dry_run=False, settings=self.settings)
        )
        self.client.assign_channel_numbers.assert_not_called()

    def test_live_sorts_and_renumbers_ascending(self):
        results = {"execution_log": [], "dry_run_results": []}
        requests = {5: {"order": "asc", "starting_number": None, "strip_numbers": True, "ignore_country": False}}
        asyncio.get_event_loop().run_until_complete(
            self.engine._sort_channel_groups(requests, self.executor, results, dry_run=False, settings=self.settings)
        )

        # "Channel 2" sorts before "Channel 10" (natural sort). Default
        # starting_number is the group's current lowest (10).
        self.client.assign_channel_numbers.assert_awaited_once_with([2, 1], 10)
        assert len(results["execution_log"]) == 1
        action = results["execution_log"][0]["actions_executed"][0]
        assert action["type"] == "sort_group"
        assert action["success"] is True
        assert "Sports" in action["description"]

    def test_live_sorts_descending(self):
        results = {"execution_log": [], "dry_run_results": []}
        requests = {5: {"order": "desc", "starting_number": 100, "strip_numbers": True, "ignore_country": False}}
        asyncio.get_event_loop().run_until_complete(
            self.engine._sort_channel_groups(requests, self.executor, results, dry_run=False, settings=self.settings)
        )
        self.client.assign_channel_numbers.assert_awaited_once_with([1, 2], 100)

    def test_dry_run_reports_without_mutating(self):
        results = {"execution_log": [], "dry_run_results": []}
        requests = {5: {"order": "asc", "starting_number": None, "strip_numbers": True, "ignore_country": False}}
        asyncio.get_event_loop().run_until_complete(
            self.engine._sort_channel_groups(requests, self.executor, results, dry_run=True, settings=self.settings)
        )

        self.client.assign_channel_numbers.assert_not_called()
        assert results["execution_log"] == []
        assert len(results["dry_run_results"]) == 1
        assert "Would sort" in results["dry_run_results"][0]["action"]
        assert results["dry_run_results"][0]["would_modify"] is True

    def test_explicit_starting_number_overrides_default(self):
        results = {"execution_log": [], "dry_run_results": []}
        requests = {5: {"order": "asc", "starting_number": 500, "strip_numbers": True, "ignore_country": False}}
        asyncio.get_event_loop().run_until_complete(
            self.engine._sort_channel_groups(requests, self.executor, results, dry_run=False, settings=self.settings)
        )
        self.client.assign_channel_numbers.assert_awaited_once_with([2, 1], 500)

    def test_multiple_groups_each_sorted_once(self):
        """Per-group dedup at the engine level: two distinct groups in the
        aggregated dict each get exactly one assign_channel_numbers call."""
        channels = [
            {"id": 1, "name": "B", "channel_group_id": 5, "channel_number": 1},
            {"id": 2, "name": "A", "channel_group_id": 5, "channel_number": 2},
            {"id": 3, "name": "D", "channel_group_id": 6, "channel_number": 1},
            {"id": 4, "name": "C", "channel_group_id": 6, "channel_number": 2},
        ]
        executor = ActionExecutor(self.client, existing_channels=channels, existing_groups=self.groups)
        results = {"execution_log": [], "dry_run_results": []}
        requests = {
            5: {"order": "asc", "starting_number": None, "strip_numbers": True, "ignore_country": False},
            6: {"order": "asc", "starting_number": None, "strip_numbers": True, "ignore_country": False},
        }
        asyncio.get_event_loop().run_until_complete(
            self.engine._sort_channel_groups(requests, executor, results, dry_run=False, settings=self.settings)
        )

        assert self.client.assign_channel_numbers.await_count == 2
        assert len(results["execution_log"]) == 2

    def test_failure_is_logged_not_raised(self):
        self.client.assign_channel_numbers = AsyncMock(side_effect=Exception("boom"))
        results = {"execution_log": [], "dry_run_results": []}
        requests = {5: {"order": "asc", "starting_number": None, "strip_numbers": True, "ignore_country": False}}
        asyncio.get_event_loop().run_until_complete(
            self.engine._sort_channel_groups(requests, self.executor, results, dry_run=False, settings=self.settings)
        )

        assert len(results["execution_log"]) == 1
        action = results["execution_log"][0]["actions_executed"][0]
        assert action["success"] is False
        assert "boom" in action["error"]

    def test_failure_reaches_run_level_failed_actions(self):
        """y3m6o.1 review (Blocker 1): a sort/renumber failure must funnel
        through the SAME run-level aggregation the other phases use, not merely
        log a success=False execution_log entry. Without this the run finalizes
        green because finalization keys terminal status off
        ``results["failed_actions"]``. Asserts BOTH the operator-visible log entry
        (preserved) AND the run-level failed_actions record are produced."""
        self.client.assign_channel_numbers = AsyncMock(side_effect=Exception("boom"))
        results = {"execution_log": [], "dry_run_results": []}
        requests = {5: {"order": "asc", "starting_number": None, "strip_numbers": True, "ignore_country": False}}
        asyncio.get_event_loop().run_until_complete(
            self.engine._sort_channel_groups(requests, self.executor, results, dry_run=False, settings=self.settings)
        )

        # (1) operator-visible execution_log entry is still present.
        assert len(results["execution_log"]) == 1
        assert results["execution_log"][0]["actions_executed"][0]["success"] is False

        # (2) the failure is aggregated into the run-level list finalization reads.
        assert "failed_actions" in results
        assert len(results["failed_actions"]) == 1
        fa = results["failed_actions"][0]
        assert fa["action_type"] == "sort_group"
        assert fa["entity_id"] == 5
        assert "boom" in fa["error"]


class TestChannelPipelineEngineExecutionTracking:
    """Tests for execution tracking methods."""

    def setup_method(self):
        """Set up test fixtures."""
        self.client = MagicMock()
        self.engine = ChannelPipelineEngine(self.client)

    def test_create_execution(self, test_session):
        """_create_execution writes a persisted row with correct mode, triggered_by, and status.

        Mutation guard: if the status were not set to 'running', or if mode/triggered_by
        were not saved to the row, these assertions fail. The original mock-only version
        passed even if the row fields were wrong.
        """
        from models import ChannelPipelineExecution

        with patch("channel_pipeline_engine.get_session", return_value=test_session):
            execution = asyncio.get_event_loop().run_until_complete(
                self.engine._create_execution(mode="execute", triggered_by="manual")
            )

        assert execution is not None
        assert execution.id is not None

        # Verify the row is really in the DB with the right fields
        row = test_session.query(ChannelPipelineExecution).filter(
            ChannelPipelineExecution.id == execution.id
        ).first()
        assert row is not None
        assert row.mode == "execute"
        assert row.triggered_by == "manual"
        assert row.status == "running"
        assert row.started_at is not None

    @patch("channel_pipeline_engine.get_session")
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

    def test_record_conflict(self, test_session):
        """_record_conflict writes a persisted ChannelPipelineConflict row with correct field values.

        Mutation guard: if conflict_type, stream_name, winning_rule_id, or losing_rule_ids were
        not set correctly, these assertions fail. The original mock-only version passed as long
        as session.add() and session.commit() were called, even with wrong field values.
        """
        from models import ChannelPipelineExecution, ChannelPipelineConflict
        import json

        # Seed a real execution row (foreign key constraint on conflict)
        execution = ChannelPipelineExecution(
            mode="execute",
            triggered_by="manual",
            started_at=__import__("datetime").datetime.utcnow(),
            status="running",
        )
        test_session.add(execution)
        test_session.commit()

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

        with patch("channel_pipeline_engine.get_session", return_value=test_session):
            asyncio.get_event_loop().run_until_complete(
                self.engine._record_conflict(
                    execution=execution,
                    stream=stream,
                    winning_rule=winning_rule,
                    losing_rules=[losing_rule],
                    conflict_type="duplicate_match",
                )
            )

        conflict = test_session.query(ChannelPipelineConflict).filter(
            ChannelPipelineConflict.execution_id == execution.id
        ).first()
        assert conflict is not None
        assert conflict.stream_id == 101
        assert conflict.stream_name == "ESPN HD"
        assert conflict.winning_rule_id == 1
        assert conflict.conflict_type == "duplicate_match"
        assert conflict.get_losing_rule_ids() == [2]

    @patch("channel_pipeline_engine.get_session")
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


class TestChannelPipelineEngineRollbackHelpers:
    """Tests for rollback helper methods."""

    def setup_method(self):
        """Set up test fixtures."""
        self.client = MagicMock()
        self.client.delete_channel = AsyncMock()
        self.client.delete_channel_group = AsyncMock()
        self.client.update_channel = AsyncMock()
        self.engine = ChannelPipelineEngine(self.client)

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
        """Rollback handles API error gracefully — and actually attempted the delete."""
        self.client.delete_channel = AsyncMock(side_effect=Exception("API error"))
        entity = {"type": "channel", "id": 1, "name": "ESPN"}

        # Should not raise
        asyncio.get_event_loop().run_until_complete(
            self.engine._rollback_created_entity(entity)
        )

        # The delete was attempted — the swallow path was actually reached.
        self.client.delete_channel.assert_called_once_with(1)

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
        """Rollback handles API error gracefully — and actually attempted the update."""
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

        # The update was attempted — the swallow path was actually reached.
        self.client.update_channel.assert_called_once_with(1, {"logo_url": "old.png"})


class TestChannelPipelineEngineIntegration:
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
        self.engine = ChannelPipelineEngine(self.client)

    @patch("channel_pipeline_engine.get_session")
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
        # ti939.1.3: a MagicMock's is_event_sync() is truthy by default,
        # which would wrongly classify this standard rule as event_sync and
        # exclude it from the run.
        mock_rule.is_event_sync.return_value = False

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
    ``channel_pipeline_engine._smart_sort_streams`` reads via ``getattr``.
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
    applied to ``channel_pipeline_engine._smart_sort_streams``.

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


def _success_stats_dict(stream_id, resolution="1920x1080", fps="25", stream_name=None):
    """Build a stats-dict for a successfully-probed stream."""
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
        "is_black_screen": False,
        "is_low_fps": False,
    }


class TestSmartSortCustomStreams:
    """bd-sgtmx / GH #244: custom streams (operator-added, non-M3U) must participate
    in the m3u_priority sort criterion via the reserved 'custom' key in
    m3u_account_priorities.  Previously these streams received priority 0 with no
    way for the operator to promote them above any M3U account.
    """

    def test_smart_sort_includes_custom_streams_in_priority(self):
        """Custom stream with m3u_account_priorities['custom']=200 sorts above
        an M3U stream with priority 100 when m3u_priority is the sole active criterion.
        """
        settings = _mk_smart_sort_settings(
            stream_sort_priority=["m3u_priority"],
            stream_sort_enabled={"m3u_priority": True},
            m3u_account_priorities={"1": 100, "custom": 200},
        )
        # sid=10 is an M3U stream from account 1 (priority 100)
        # sid=20 is a custom stream (no m3u_account_id) — should use "custom" priority 200
        stats_cache = {
            10: _success_stats_dict(10, stream_name="M3U Stream"),
            20: _success_stats_dict(20, stream_name="Custom Stream"),
        }
        stream_m3u_map = {10: 1}  # sid=20 absent → custom stream

        result = _smart_sort_streams(
            [10, 20], stats_cache, stream_m3u_map=stream_m3u_map,
            channel_name="sgtmx-custom-first", settings=settings,
        )
        assert result == [20, 10], (
            f"Expected custom stream (priority 200) to rank above M3U stream "
            f"(priority 100), got {result}"
        )

    def test_smart_sort_custom_stream_priority_does_not_displace_m3u(self):
        """With no 'custom' key set (default 0), custom streams rank below any M3U
        stream whose account has a positive priority.  Existing silent-zero behaviour
        is preserved for operators who never configure the 'custom' key.
        """
        settings = _mk_smart_sort_settings(
            stream_sort_priority=["m3u_priority"],
            stream_sort_enabled={"m3u_priority": True},
            m3u_account_priorities={"1": 50},  # no "custom" key
        )
        stats_cache = {
            10: _success_stats_dict(10, stream_name="M3U Stream"),
            20: _success_stats_dict(20, stream_name="Custom Stream"),
        }
        stream_m3u_map = {10: 1}  # sid=20 is custom

        result = _smart_sort_streams(
            [10, 20], stats_cache, stream_m3u_map=stream_m3u_map,
            channel_name="sgtmx-m3u-first", settings=settings,
        )
        assert result == [10, 20], (
            f"Expected M3U stream (priority 50) to rank above custom stream "
            f"(default priority 0), got {result}"
        )

    def test_smart_sort_existing_behavior_unchanged_for_pure_m3u_inputs(self):
        """Regression guard: a channel with only M3U streams sorts identically to
        before the bd-sgtmx fix.  No custom stream is present so the 'custom' key
        code path is never reached.
        """
        settings = _mk_smart_sort_settings(
            stream_sort_priority=["m3u_priority"],
            stream_sort_enabled={"m3u_priority": True},
            m3u_account_priorities={"1": 100, "2": 50},
        )
        stats_cache = {
            10: _success_stats_dict(10, stream_name="M3U Acct-1 Stream"),
            20: _success_stats_dict(20, stream_name="M3U Acct-2 Stream"),
        }
        stream_m3u_map = {10: 1, 20: 2}  # both M3U, no custom stream

        result = _smart_sort_streams(
            [10, 20], stats_cache, stream_m3u_map=stream_m3u_map,
            channel_name="sgtmx-pure-m3u", settings=settings,
        )
        assert result == [10, 20], (
            f"Expected M3U account 1 (priority 100) before account 2 (priority 50), "
            f"got {result}"
        )

    def test_smart_sort_custom_stream_priority_respected_within_deprioritized_bucket(self):
        """bd-sgtmx: even when a custom stream lands in the deprioritized bucket
        (no probe stats), its m3u_priority value from 'custom' key is used as a
        tiebreaker within that bucket — mirrors the bd-bqpq0 within-bucket ordering.
        """
        settings = _mk_smart_sort_settings(
            stream_sort_priority=["m3u_priority"],
            stream_sort_enabled={"m3u_priority": True},
            m3u_account_priorities={"1": 10, "custom": 50},
            deprioritize_failed_streams=True,
            failed_stream_sort_order=["failed", "black_screen", "low_fps"],
        )
        # Both streams have no probe stats — both land in the failed bucket.
        # Within the failed bucket, the custom stream (priority 50) should lead.
        stats_cache = {}  # no probe data for either stream

        result = _smart_sort_streams(
            [10, 20], stats_cache,
            stream_m3u_map={10: 1},  # sid=20 is custom
            channel_name="sgtmx-deprioritzed-bucket",
            settings=settings,
        )
        assert result == [20, 10], (
            f"Expected custom stream (priority 50) to lead over M3U stream "
            f"(priority 10) within the deprioritized bucket, got {result}"
        )


class TestSmartSortCustomStreamsCriterion:
    """bead ap1ud / GH #244: the dedicated ``custom_streams`` Smart Sort criterion.

    Binary criterion — a stream scores 1 if its id is in ``custom_stream_ids``
    (Dispatcharr is_custom), else 0. When ranked as the top active criterion,
    custom streams sort to the top; ties fall through to the next criterion.
    Mirrors the prober's TestSmartSortCustomStreamsCriterion.
    """

    def test_custom_stream_sorts_first_when_top_criterion(self):
        """With custom_streams as the sole/top criterion, the custom stream leads."""
        settings = _mk_smart_sort_settings(
            stream_sort_priority=["custom_streams"],
            stream_sort_enabled={"custom_streams": True},
        )
        stats_cache = {
            10: _success_stats_dict(10, stream_name="M3U Stream"),
            20: _success_stats_dict(20, stream_name="Custom Stream"),
        }
        result = _smart_sort_streams(
            [10, 20], stats_cache, stream_m3u_map={10: 1, 20: 2},
            channel_name="ap1ud-custom-first", settings=settings,
            custom_stream_ids={20},
        )
        assert result == [20, 10], (
            f"Expected custom stream (id=20) to sort first, got {result}"
        )

    def test_custom_streams_disabled_has_no_effect(self):
        """When custom_streams is disabled, it does not influence ordering."""
        settings = _mk_smart_sort_settings(
            stream_sort_priority=["custom_streams", "resolution"],
            stream_sort_enabled={"custom_streams": False, "resolution": True},
        )
        # id=10 is custom but lower resolution; id=20 is M3U but higher resolution.
        stats_cache = {
            10: _success_stats_dict(10, resolution="1280x720", stream_name="Custom"),
            20: _success_stats_dict(20, resolution="1920x1080", stream_name="M3U"),
        }
        result = _smart_sort_streams(
            [10, 20], stats_cache, stream_m3u_map={20: 1},
            channel_name="ap1ud-custom-disabled", settings=settings,
            custom_stream_ids={10},
        )
        # Sorted by resolution only — higher res (id=20) first.
        assert result == [20, 10], (
            f"Expected resolution-only ordering [20, 10] with custom_streams "
            f"disabled, got {result}"
        )

    def test_pure_m3u_channel_unaffected(self):
        """A channel with no custom streams is unaffected by the criterion."""
        settings = _mk_smart_sort_settings(
            stream_sort_priority=["custom_streams", "resolution"],
            stream_sort_enabled={"custom_streams": True, "resolution": True},
        )
        stats_cache = {
            10: _success_stats_dict(10, resolution="1920x1080", stream_name="M3U Hi"),
            20: _success_stats_dict(20, resolution="1280x720", stream_name="M3U Lo"),
        }
        result = _smart_sort_streams(
            [10, 20], stats_cache, stream_m3u_map={10: 1, 20: 2},
            channel_name="ap1ud-pure-m3u", settings=settings,
            custom_stream_ids=set(),  # no custom streams
        )
        # No custom streams → custom_streams criterion is a constant 0; falls
        # through to resolution: higher res (id=10) first.
        assert result == [10, 20], (
            f"Expected resolution ordering [10, 20] for pure-M3U channel, got {result}"
        )

    def test_criterion_inert_when_custom_stream_ids_not_supplied(self):
        """When custom_stream_ids is omitted, custom_streams scores 0 everywhere
        and the next criterion (resolution) decides — graceful degradation."""
        settings = _mk_smart_sort_settings(
            stream_sort_priority=["custom_streams", "resolution"],
            stream_sort_enabled={"custom_streams": True, "resolution": True},
        )
        stats_cache = {
            10: _success_stats_dict(10, resolution="1920x1080", stream_name="Hi"),
            20: _success_stats_dict(20, resolution="1280x720", stream_name="Lo"),
        }
        result = _smart_sort_streams(
            [10, 20], stats_cache, stream_m3u_map={10: 1, 20: 2},
            channel_name="ap1ud-inert", settings=settings,
            # custom_stream_ids intentionally omitted
        )
        assert result == [10, 20], (
            f"Expected resolution ordering [10, 20] when custom_stream_ids "
            f"omitted, got {result}"
        )

    def test_custom_tie_falls_through_to_next_criterion(self):
        """Two custom streams tie on custom_streams; resolution breaks the tie."""
        settings = _mk_smart_sort_settings(
            stream_sort_priority=["custom_streams", "resolution"],
            stream_sort_enabled={"custom_streams": True, "resolution": True},
        )
        stats_cache = {
            10: _success_stats_dict(10, resolution="1280x720", stream_name="Custom Lo"),
            20: _success_stats_dict(20, resolution="1920x1080", stream_name="Custom Hi"),
        }
        result = _smart_sort_streams(
            [10, 20], stats_cache, stream_m3u_map={},
            channel_name="ap1ud-custom-tie", settings=settings,
            custom_stream_ids={10, 20},  # both custom
        )
        # Both custom (tie); resolution breaks tie → higher res (id=20) first.
        assert result == [20, 10], (
            f"Expected resolution tiebreak [20, 10] among custom streams, got {result}"
        )


class TestSmartSortCatchupCriterion:
    """enhancedchannelmanager-jnbka / GH #652: catch-up-enabled streams rank first."""

    def test_catchup_stream_sorts_first_when_top_criterion(self):
        settings = _mk_smart_sort_settings(
            stream_sort_priority=["catchup"],
            stream_sort_enabled={"catchup": True},
        )
        stats_cache = {
            10: _success_stats_dict(10, stream_name="Live"),
            20: _success_stats_dict(20, stream_name="Catch-up"),
        }

        result = _smart_sort_streams(
            [10, 20], stats_cache, stream_m3u_map={},
            channel_name="gh652-catchup-first", settings=settings,
            catchup_stream_ids={20},
        )

        assert result == [20, 10]

    def test_catchup_tie_falls_through_to_resolution(self):
        settings = _mk_smart_sort_settings(
            stream_sort_priority=["catchup", "resolution"],
            stream_sort_enabled={"catchup": True, "resolution": True},
        )
        stats_cache = {
            10: _success_stats_dict(10, resolution="1280x720"),
            20: _success_stats_dict(20, resolution="1920x1080"),
        }

        result = _smart_sort_streams(
            [10, 20], stats_cache, stream_m3u_map={},
            channel_name="gh652-catchup-tie", settings=settings,
            catchup_stream_ids={10, 20},
        )

        assert result == [20, 10]


class TestRunPipelineCreateChannelMergeChannelsTouched:
    """bd-0emgo.4 real-path regression: create_channel + if_exists=merge dry-run.

    A live dry-run of a ``create_channel`` rule with ``if_exists=merge`` reported
    ``streams_merged=26`` but ``channels_touched=0`` — inconsistent. Two earlier
    fix attempts populated the per-channel tracking dict at specific CALL SITES
    (``_execute_merge_streams`` and the ``_execute_create_channel`` if_exists=merge
    branch), and their tests pre-seeded ``existing_channels`` so the FIRST stream
    of each name already merged. But the real run created the channel for the
    first stream and merged the 2nd+ streams into the *would-be-created* channel —
    a path that, depending on lookup/normalization, the call-site additions did
    not reliably cover.

    These tests drive the REAL pipeline (``run_pipeline`` -> ``_process_streams``
    -> ``executor.execute`` -> ``_execute_create_channel`` -> ``_add_stream_to_channel``)
    with channels that do NOT pre-exist, so the merges flow through the create
    path exactly as in the live run. They assert ``channels_touched`` is the
    distinct count of channels that received a merged stream — and is NOT 0.
    """

    def setup_method(self):
        self.client = MagicMock()
        # No pre-existing channels: every channel is created during the run,
        # so 2nd+ same-name streams merge into a would-be-created channel.
        self.client.get_channels = AsyncMock(return_value={"count": 0, "results": []})
        self.client.get_channel_groups = AsyncMock(return_value=[])
        self.client.update_channel = AsyncMock()
        # create_channel is only called in LIVE mode; dry-run never hits it.
        self._next_live_id = iter(range(1000, 2000))
        self.client.create_channel = AsyncMock(
            side_effect=lambda data: {
                "id": next(self._next_live_id),
                "name": data["name"],
                "channel_number": data.get("channel_number"),
                "channel_group_id": data.get("channel_group_id"),
                "streams": data.get("streams", []),
            }
        )
        self.engine = ChannelPipelineEngine(self.client)

    def _make_rule(self, if_exists="merge"):
        """A real (unpersisted) ChannelPipelineRule: create_channel + if_exists=merge.

        Using a real model instance (not a MagicMock) keeps sort_field=None,
        get_managed_channel_ids()=[], get_normalization_group_ids()=[], etc., so
        the pipeline's sort/renumber passes stay inert and the merge path is the
        only thing exercised.
        """
        from models import ChannelPipelineRule

        rule = ChannelPipelineRule()
        rule.id = 1
        rule.name = "Create+Merge Rule"
        rule.priority = 0
        rule.enabled = True
        rule.m3u_account_id = None
        rule.target_group_id = None
        rule.stop_on_first_match = True
        rule.match_scope_target_group = False
        rule.match_scope_group_id = None
        rule.skip_struck_streams = False
        rule.set_conditions([{"type": "always"}])
        rule.set_actions([{
            "type": "create_channel",
            "name_template": "{stream_name}",
            "if_exists": if_exists,
        }])
        return rule

    def _make_streams(self, names_to_ids):
        """Build StreamContexts. names_to_ids: list of (name, stream_id)."""
        return [
            StreamContext(stream_id=sid, stream_name=name, m3u_account_id=1)
            for name, sid in names_to_ids
        ]

    def _run(self, rule, streams, dry_run):
        """Drive the REAL pipeline with the rule/streams, stubbing only the
        DB/IO lifecycle boundaries (rules load, stream fetch, execution
        persistence). _process_streams and the whole executor chain run for real.
        """
        self.engine._load_rules = AsyncMock(return_value=[rule])
        self.engine._fetch_streams = AsyncMock(return_value=streams)
        self.engine._create_execution = AsyncMock(return_value=MagicMock(id=1, mode=None))
        self.engine._save_execution = AsyncMock()
        self.engine._update_rule_stats = AsyncMock()

        with patch("channel_pipeline_engine.get_session") as mock_get_session:
            mock_get_session.return_value = MagicMock()
            return asyncio.get_event_loop().run_until_complete(
                self.engine.run_pipeline(dry_run=dry_run)
            )

    def test_dry_run_create_channel_merge_channels_touched_not_zero(self):
        """DRY-RUN: streams_merged>=1 AND channels_touched == distinct merged-into
        channels (NOT 0) — the exact live inconsistency.

        Inputs share names so 2nd+ streams merge into would-be-created channels:
          ESPN x3 -> 1 created + 2 merged
          CNN  x2 -> 1 created + 1 merged
          FOX  x1 -> 1 created + 0 merged (not "touched by a merge")
        => streams_merged == 3, channels_touched == 2 (ESPN, CNN).
        """
        rule = self._make_rule(if_exists="merge")
        streams = self._make_streams([
            ("ESPN", 601), ("ESPN", 602), ("ESPN", 603),
            ("CNN", 701), ("CNN", 702),
            ("FOX", 801),
        ])

        result = self._run(rule, streams, dry_run=True)

        assert result["streams_merged"] == 3, (
            f"expected 3 merges (2 ESPN + 1 CNN), got {result['streams_merged']}"
        )
        # The bug: channels_touched stays 0 even though merges happened.
        assert result["channels_touched"] != 0, (
            f"channels_touched must not be 0 when streams_merged="
            f"{result['streams_merged']} (live create_channel+merge dry-run bug)"
        )
        assert result["channels_touched"] == 2, (
            f"expected 2 distinct channels touched by merges (ESPN, CNN), "
            f"got {result['channels_touched']} "
            f"(streams_merged={result['streams_merged']})"
        )

    def test_live_create_channel_merge_channels_touched_consistent(self):
        """LIVE: same scenario through the real create_channel API path.

        channels_touched must still equal the distinct merged-into channel count.
        """
        rule = self._make_rule(if_exists="merge")
        streams = self._make_streams([
            ("ESPN", 901), ("ESPN", 902), ("ESPN", 903),
            ("CNN", 911), ("CNN", 912),
            ("FOX", 921),
        ])

        with patch("channel_pipeline_executor.journal.log_entries"):
            result = self._run(rule, streams, dry_run=False)

        assert result["streams_merged"] == 3, (
            f"expected 3 merges, got {result['streams_merged']}"
        )
        assert result["channels_touched"] == 2, (
            f"expected 2 distinct channels touched by merges, "
            f"got {result['channels_touched']}"
        )

    def test_live_merge_journal_flushes_when_later_pass_raises(self):
        """Buffered merge journal rows flush when a later pipeline pass raises."""
        rule = self._make_rule(if_exists="merge")
        streams = self._make_streams([
            ("ESPN", 901), ("ESPN", 902), ("ESPN", 903),
            ("CNN", 911), ("CNN", 912),
            ("FOX", 921),
        ])
        self.engine._reorder_channel_streams = AsyncMock(
            side_effect=RuntimeError("reorder failed")
        )

        with patch("channel_pipeline_executor.journal.log_entries") as mock_log:
            with pytest.raises(RuntimeError, match="reorder failed"):
                self._run(rule, streams, dry_run=False)

        mock_log.assert_called_once()
        entries = mock_log.call_args.kwargs["entries"]
        assert len(entries) == 3
        assert {entry["batch_id"] for entry in entries} == {"1"}


class TestRunLevelFailedActionStatus:
    """y3m6o.1 (0152) — THE core regression the first pass MISSED.

    A run in which an executed action FAILS must finalize as
    ``completed_with_errors`` (a distinct terminal outcome, NOT green
    ``completed``) AND the top-level API result must be non-success. Drives the
    REAL pipeline end to end (run_pipeline -> _process_streams ->
    executor.execute -> _execute_assign_channel_profile) with a rule whose
    assign_channel_profile action fails a profile update — this is a run-level
    assertion on both the stored ``execution.status`` and the returned dict, not
    a unit probe of the action result (which the 0151 pass mistook for
    coverage)."""

    def setup_method(self):
        self.client = MagicMock()
        self.client.get_channels = AsyncMock(return_value={"count": 0, "results": []})
        self.client.get_channel_groups = AsyncMock(return_value=[])
        self.client.update_channel = AsyncMock()
        self.client.get_channel_profiles = AsyncMock(
            return_value=[{"id": 1}, {"id": 2}, {"id": 3}]
        )
        # Disabling profile 2 fails => assign_channel_profile returns a partial
        # failure (success=False) for the created channel.
        def _update_profile(pid, channel_id, body):
            if pid == 2:
                raise RuntimeError("profile 2 patch failed")
            return None
        self.client.update_profile_channel = AsyncMock(side_effect=_update_profile)
        self._next_id = iter(range(1000, 2000))
        self.client.create_channel = AsyncMock(
            side_effect=lambda data: {
                "id": next(self._next_id),
                "name": data["name"],
                "channel_number": data.get("channel_number"),
                "channel_group_id": data.get("channel_group_id"),
                "streams": data.get("streams", []),
            }
        )
        self.engine = ChannelPipelineEngine(self.client)

    def _make_rule(self):
        from models import ChannelPipelineRule

        rule = ChannelPipelineRule()
        rule.id = 1
        rule.name = "Profile Rule"
        rule.priority = 0
        rule.enabled = True
        rule.m3u_account_id = None
        rule.target_group_id = None
        rule.stop_on_first_match = True
        rule.match_scope_target_group = False
        rule.match_scope_group_id = None
        rule.skip_struck_streams = False
        rule.set_conditions([{"type": "always"}])
        rule.set_actions([
            {"type": "create_channel", "name_template": "{stream_name}"},
            {"type": "assign_channel_profile", "channel_profile_ids": [1]},
        ])
        return rule

    def _run(self, rule, streams, dry_run=False):
        self.engine._load_rules = AsyncMock(return_value=[rule])
        self.engine._fetch_streams = AsyncMock(return_value=streams)
        self.exec_mock = MagicMock(id=1, mode="execute")
        self.engine._create_execution = AsyncMock(return_value=self.exec_mock)
        self.engine._save_execution = AsyncMock()
        self.engine._update_rule_stats = AsyncMock()
        with patch("channel_pipeline_engine.get_session") as mock_get_session, \
                patch("channel_pipeline_executor.journal.log_entries"):
            mock_get_session.return_value = MagicMock()
            return asyncio.get_event_loop().run_until_complete(
                self.engine.run_pipeline(dry_run=dry_run)
            )

    def test_failed_action_finalizes_completed_with_errors(self):
        rule = self._make_rule()
        streams = [
            StreamContext(stream_id=601, stream_name="ESPN", m3u_account_id=1)
        ]

        result = self._run(rule, streams, dry_run=False)

        # (1) the STORED execution row status is the new errored terminal state.
        assert self.exec_mock.status == "completed_with_errors"
        # (2) the top-level API result is non-success.
        assert result["success"] is False
        assert result["status"] == "completed_with_errors"
        assert result["failed_action_count"] >= 1
        # The error_message summary names the failure + safe-retry guidance.
        msg = (self.exec_mock.error_message or "").lower()
        assert "failed" in msg
        assert "retry" in msg

    def test_clean_run_finalizes_completed(self):
        """Control: same rule but all profile writes succeed => green
        ``completed`` + success True. Proves the errored status is not applied
        spuriously."""
        self.client.update_profile_channel = AsyncMock()  # all succeed
        rule = self._make_rule()
        streams = [
            StreamContext(stream_id=601, stream_name="ESPN", m3u_account_id=1)
        ]

        result = self._run(rule, streams, dry_run=False)

        assert self.exec_mock.status == "completed"
        assert result["success"] is True
        assert result["failed_action_count"] == 0

    def test_dry_run_unavailable_universe_finalizes_completed_with_errors(self):
        """y3m6o.1 Finding 2 (0152) at run level: a DRY RUN whose profile-universe
        fetch FAILS must surface the blocking preview as completed_with_errors,
        not a rosy green preview. The dry-run assign action returns the
        universe-unavailable failure, which aggregates into failed_actions."""
        self.client.get_channel_profiles = AsyncMock(
            side_effect=RuntimeError("dispatcharr down")
        )
        rule = self._make_rule()
        streams = [
            StreamContext(stream_id=601, stream_name="ESPN", m3u_account_id=1)
        ]

        result = self._run(rule, streams, dry_run=True)

        assert self.exec_mock.status == "completed_with_errors"
        assert result["success"] is False
        assert result["failed_action_count"] >= 1
        # Nothing was written even in the failure preview.
        self.client.update_profile_channel.assert_not_called()


class TestSortGroupFailureRunStatus:
    """y3m6o.1 review (Blocker 1) at RUN level: a Pass 3.6 sort/renumber failure
    must finalize the whole run ``completed_with_errors`` + non-success — NOT a
    green ``completed`` with only a success=False execution_log entry. Drives the
    REAL ``run_pipeline`` end to end: a rule with a ``sort_group`` action queues a
    2-channel group for the post-run sort pass, and ``assign_channel_numbers``
    raises, so the caught failure must reach ``results["failed_actions"]`` and
    flip the terminal status."""

    def setup_method(self):
        self.client = MagicMock()
        # Two pre-existing channels in group 5 → the sort pass has ≥2 channels
        # and actually calls assign_channel_numbers (which we make fail).
        self._channels = [
            {"id": 101, "name": "Channel B", "channel_group_id": 5, "channel_number": 10},
            {"id": 102, "name": "Channel A", "channel_group_id": 5, "channel_number": 11},
        ]
        self.client.get_channels = AsyncMock(
            return_value={"count": 2, "results": self._channels}
        )
        self.client.get_channel_groups = AsyncMock(
            return_value=[{"id": 5, "name": "Sports"}]
        )
        self.client.get_channel_profiles = AsyncMock(return_value=[{"id": 1}])
        self.client.update_channel = AsyncMock()
        # The Pass 3.6 renumber call fails — the whole point of the regression.
        self.client.assign_channel_numbers = AsyncMock(
            side_effect=RuntimeError("dispatcharr renumber rejected")
        )
        self.engine = ChannelPipelineEngine(self.client)

    def _make_sort_rule(self):
        from models import ChannelPipelineRule

        rule = ChannelPipelineRule()
        rule.id = 1
        rule.name = "Sort Sports"
        rule.priority = 0
        rule.enabled = True
        rule.m3u_account_id = None
        rule.target_group_id = None
        rule.stop_on_first_match = True
        rule.match_scope_target_group = False
        rule.match_scope_group_id = None
        rule.skip_struck_streams = False
        rule.set_conditions([{"type": "always"}])
        # sort_group with an explicit group_id resolves without needing a prior
        # create_channel — it queues group 5 for the post-run sort pass.
        rule.set_actions([
            {"type": "sort_group", "group_id": 5, "order": "asc"},
        ])
        return rule

    def _run(self, rule, streams, dry_run=False):
        self.engine._load_rules = AsyncMock(return_value=[rule])
        self.engine._fetch_streams = AsyncMock(return_value=streams)
        self.exec_mock = MagicMock(id=1, mode="execute")
        self.engine._create_execution = AsyncMock(return_value=self.exec_mock)
        self.engine._save_execution = AsyncMock()
        self.engine._update_rule_stats = AsyncMock()
        with patch("channel_pipeline_engine.get_session") as mock_get_session, \
                patch("channel_pipeline_executor.journal.log_entries"):
            mock_get_session.return_value = MagicMock()
            return asyncio.get_event_loop().run_until_complete(
                self.engine.run_pipeline(dry_run=dry_run)
            )

    def test_sort_group_failure_finalizes_completed_with_errors(self):
        rule = self._make_sort_rule()
        streams = [
            StreamContext(stream_id=601, stream_name="ESPN", m3u_account_id=1)
        ]

        result = self._run(rule, streams, dry_run=False)

        # The renumber pass was actually attempted (2 channels in the group).
        self.client.assign_channel_numbers.assert_awaited()
        # (1) the STORED execution row is the errored terminal state, not green.
        assert self.exec_mock.status == "completed_with_errors"
        # (2) the top-level API result is non-success and counts the failure.
        assert result["success"] is False
        assert result["status"] == "completed_with_errors"
        assert result["failed_action_count"] >= 1
        # (3) the failure carries the sort_group phase + underlying error.
        fa = [f for f in result["failed_actions"] if f["action_type"] == "sort_group"]
        assert len(fa) == 1
        assert "renumber rejected" in fa[0]["error"]


class TestRuleRenumberFailureRunStatus:
    """y3m6o.1 review (Blocker A) at RUN level: a Pass 3 rule-level renumber
    failure (a rule with ``sort_field`` set whose ``assign_channel_numbers``
    raises) must finalize ``completed_with_errors`` + non-success — NOT green.
    This is a DIFFERENT code path from sort_group (Pass 3, not Pass 3.6): the
    sort_group test exercises a sort_group action and skips the rule-level
    renumber pass entirely, so this path was previously uncovered. Drives the
    REAL ``run_pipeline`` end to end: a rule with a create_channel action (fixed
    starting number) + sort_field creates 2 owned channels, then Pass 3 renumbers
    them and the renumber raises."""

    def setup_method(self):
        self.client = MagicMock()
        self.client.get_channels = AsyncMock(return_value={"count": 0, "results": []})
        self.client.get_channel_groups = AsyncMock(return_value=[])
        self.client.update_channel = AsyncMock()
        self.client.get_channel_profiles = AsyncMock(return_value=[{"id": 1}])
        self._next_id = iter(range(1000, 2000))
        self.client.create_channel = AsyncMock(
            side_effect=lambda data: {
                "id": next(self._next_id),
                "name": data["name"],
                "channel_number": data.get("channel_number"),
                "channel_group_id": data.get("channel_group_id"),
                "streams": data.get("streams", []),
            }
        )
        # The Pass 3 rule-level renumber call fails.
        self.client.assign_channel_numbers = AsyncMock(
            side_effect=RuntimeError("dispatcharr renumber rejected")
        )
        self.engine = ChannelPipelineEngine(self.client)

    def _make_rule(self):
        from models import ChannelPipelineRule

        rule = ChannelPipelineRule()
        rule.id = 1
        rule.name = "Renumber Rule"
        rule.priority = 0
        rule.enabled = True
        rule.m3u_account_id = None
        rule.target_group_id = None
        rule.stop_on_first_match = True
        rule.match_scope_target_group = False
        rule.match_scope_group_id = None
        rule.skip_struck_streams = False
        # sort_field triggers the Pass 3 rule-level renumber; a FIXED starting
        # number (not "auto") makes _get_rule_starting_number return non-None so
        # the pass actually runs.
        rule.sort_field = "channel_name"
        rule.sort_order = "asc"
        rule.set_conditions([{"type": "always"}])
        rule.set_actions([
            {"type": "create_channel", "name_template": "{stream_name}",
             "channel_number": 100},
        ])
        return rule

    def _run(self, rule, streams, dry_run=False):
        self.engine._load_rules = AsyncMock(return_value=[rule])
        self.engine._fetch_streams = AsyncMock(return_value=streams)
        self.exec_mock = MagicMock(id=1, mode="execute")
        self.engine._create_execution = AsyncMock(return_value=self.exec_mock)
        self.engine._save_execution = AsyncMock()
        self.engine._update_rule_stats = AsyncMock()
        with patch("channel_pipeline_engine.get_session") as mock_get_session, \
                patch("channel_pipeline_executor.journal.log_entries"):
            mock_get_session.return_value = MagicMock()
            return asyncio.get_event_loop().run_until_complete(
                self.engine.run_pipeline(dry_run=dry_run)
            )

    def test_rule_renumber_failure_finalizes_completed_with_errors(self):
        rule = self._make_rule()
        # Two streams => two channels created and owned by the rule, so Pass 3
        # (len >= 2) actually renumbers.
        streams = [
            StreamContext(stream_id=601, stream_name="ESPN", m3u_account_id=1),
            StreamContext(stream_id=602, stream_name="CNN", m3u_account_id=1),
        ]

        result = self._run(rule, streams, dry_run=False)

        # The rule-level renumber pass was actually attempted.
        self.client.assign_channel_numbers.assert_awaited()
        # (1) stored execution row is the errored terminal state, not green.
        assert self.exec_mock.status == "completed_with_errors"
        # (2) top-level result is non-success + counts the failure.
        assert result["success"] is False
        assert result["status"] == "completed_with_errors"
        assert result["failed_action_count"] >= 1
        # (3) the failure carries the renumber_channels phase + rule identity.
        fa = [
            f for f in result["failed_actions"]
            if f["action_type"] == "renumber_channels"
        ]
        assert len(fa) == 1
        assert fa[0]["rule_id"] == 1
        assert fa[0]["rule_name"] == "Renumber Rule"
        assert "renumber rejected" in fa[0]["error"]


class TestOrphanCleanupRenumberFailureRunStatus:
    """y3m6o.1 review (Blocker B) at RUN level: a renumber-after-orphan-cleanup
    failure (_reconcile_orphans) previously ONLY logged — no execution_log entry,
    no aggregation — so it was completely silent and finalized green. It must now
    finalize ``completed_with_errors`` + populate failed_actions AND write a
    success=False execution_log entry. Drives the REAL ``run_pipeline``: the rule
    has a pre-run managed set containing an orphan that no longer matches, so the
    orphan is deleted and the post-cleanup renumber runs — and raises.

    Also pins the managed_channel_ids JUDGMENT CALL: the membership ledger is
    ADVANCED to the current set even though the (cosmetic) renumber failed."""

    def setup_method(self):
        self.client = MagicMock()
        # Pre-existing orphan channel 999 (owned by the rule last run) that will
        # not be matched again this run => it becomes an orphan and is deleted.
        self._orphan = {
            "id": 999, "name": "Old Channel", "channel_group_id": 5,
            "channel_number": 50,
        }
        self.client.get_channels = AsyncMock(
            return_value={"count": 1, "results": [self._orphan]}
        )
        self.client.get_channel_groups = AsyncMock(
            return_value=[{"id": 5, "name": "Sports"}]
        )
        self.client.update_channel = AsyncMock()
        self.client.delete_channel = AsyncMock()
        self.client.get_channel_profiles = AsyncMock(return_value=[{"id": 1}])
        self._next_id = iter(range(1000, 2000))
        self.client.create_channel = AsyncMock(
            side_effect=lambda data: {
                "id": next(self._next_id),
                "name": data["name"],
                "channel_number": data.get("channel_number"),
                "channel_group_id": data.get("channel_group_id"),
                "streams": data.get("streams", []),
            }
        )
        # The post-orphan-cleanup renumber call fails.
        self.client.assign_channel_numbers = AsyncMock(
            side_effect=RuntimeError("post-cleanup renumber rejected")
        )
        self.engine = ChannelPipelineEngine(self.client)

    def _make_rule(self):
        from models import ChannelPipelineRule

        rule = ChannelPipelineRule()
        rule.id = 1
        rule.name = "Orphan Rule"
        rule.priority = 0
        rule.enabled = True
        rule.m3u_account_id = None
        rule.target_group_id = 5
        rule.stop_on_first_match = True
        rule.match_scope_target_group = False
        rule.match_scope_group_id = None
        rule.skip_struck_streams = False
        rule.orphan_action = "delete"
        # No sort_field: keep Pass 3 out of it so ONLY the post-cleanup renumber
        # fires. A fixed create starting number makes _get_rule_starting_number
        # return non-None so the post-cleanup renumber actually runs.
        rule.set_conditions([{"type": "always"}])
        rule.set_actions([
            {"type": "create_channel", "name_template": "{stream_name}",
             "channel_number": 100},
        ])
        # Pre-run managed set: channel 999 was owned last run. It still exists in
        # Dispatcharr (returned by get_channels) so it is a live orphan, not a
        # stale one, and gets deleted + triggers the post-cleanup renumber.
        rule.set_managed_channel_ids([999])
        return rule

    def _run(self, rule, streams, dry_run=False):
        self.engine._load_rules = AsyncMock(return_value=[rule])
        self.engine._fetch_streams = AsyncMock(return_value=streams)
        self.exec_mock = MagicMock(id=1, mode="execute")
        self.engine._create_execution = AsyncMock(return_value=self.exec_mock)
        self.engine._save_execution = AsyncMock()
        self.engine._update_rule_stats = AsyncMock()
        with patch("channel_pipeline_engine.get_session") as mock_get_session, \
                patch("channel_pipeline_executor.journal.log_entries"):
            mock_get_session.return_value = MagicMock()
            return asyncio.get_event_loop().run_until_complete(
                self.engine.run_pipeline(dry_run=dry_run)
            )

    def test_orphan_cleanup_renumber_failure_finalizes_completed_with_errors(self):
        rule = self._make_rule()
        rule.sort_field = "stream_name"
        # One NEW stream => creates channel 1000 (current), so 999 (previous)
        # becomes an orphan; after deleting it the remaining [1000] is renumbered.
        streams = [
            StreamContext(stream_id=601, stream_name="ESPN", m3u_account_id=1)
        ]

        result = self._run(rule, streams, dry_run=False)

        # The orphan was deleted and the post-cleanup renumber was attempted.
        self.client.delete_channel.assert_awaited_with(999)
        self.client.assign_channel_numbers.assert_awaited()
        # (1) stored execution row is the errored terminal state, not green.
        assert self.exec_mock.status == "completed_with_errors"
        # (2) top-level result is non-success + counts the failure.
        assert result["success"] is False
        assert result["status"] == "completed_with_errors"
        assert result["failed_action_count"] >= 1
        # (3) the failure is aggregated with the renumber_channels phase + rule.
        fa = [
            f for f in result["failed_actions"]
            if f["action_type"] == "renumber_channels"
        ]
        assert len(fa) == 1
        assert fa[0]["rule_id"] == 1
        assert "post-cleanup renumber rejected" in fa[0]["error"]
        # (4) parity with the other renumber sites: a success=False execution_log
        # entry is also present (previously this site logged NOTHING).
        renumber_log = [
            entry for entry in result["execution_log"]
            for a in entry.get("actions_executed", [])
            if a.get("type") == "renumber_channels" and a.get("success") is False
        ]
        assert len(renumber_log) >= 1
        # (5) JUDGMENT CALL: the membership ledger is ADVANCED to the current set
        # despite the cosmetic renumber failing. managed_channel_ids tracks
        # OWNERSHIP (drives orphan detection), orthogonal to numbering; the
        # renumber passes re-run unconditionally next execution so the failed
        # renumber is retried regardless. 999 (deleted orphan) is dropped; 1000
        # (the current channel) is retained.
        managed = set(rule.get_managed_channel_ids())
        assert 999 not in managed
        assert 1000 in managed

    def test_no_sort_preserves_existing_manual_numbers_during_orphan_cleanup(self):
        """GH #833: cleanup may delete debt, but no-sort must not renumber survivors.

        Drive the real executor/pipeline path: a fixed-number create action still
        allocates the genuinely new channel, while the pre-existing managed
        channel keeps its manually assigned number because no channel sort was
        requested.
        """
        existing = {
            "id": 1000,
            "name": "Existing",
            "channel_group_id": 5,
            "channel_number": 777,
            "streams": [601],
        }
        self.client.get_channels = AsyncMock(
            return_value={"count": 2, "results": [self._orphan, existing]}
        )
        self.client.assign_channel_numbers = AsyncMock()
        rule = self._make_rule()
        rule.sort_field = None
        rule.set_managed_channel_ids([999, 1000])
        streams = [
            StreamContext(stream_id=601, stream_name="Existing", m3u_account_id=1),
            StreamContext(stream_id=602, stream_name="New", m3u_account_id=1),
        ]

        result = self._run(rule, streams, dry_run=False)

        assert result["success"] is True
        self.client.delete_channel.assert_awaited_with(999)
        self.client.assign_channel_numbers.assert_not_awaited()
        created_payloads = [call.args[0] for call in self.client.create_channel.await_args_list]
        assert any(payload.get("channel_number") == 100 for payload in created_payloads)


class TestRunLevelFailureAggregationFullCoverage:
    """y3m6o.1 review — FULL failure aggregation (the 0152 pass only routed
    assign_channel_profile action failures). Every executed-action/phase failure
    must finalize the run ``completed_with_errors`` + non-success. Each test
    drives the REAL ``run_pipeline`` path end to end. Finding 3 (non-reversible
    disclosure) and Finding 2 (compound capped+failed) share this harness."""

    def setup_method(self):
        from config import DispatcharrSettings

        self.client = MagicMock()
        self.client.get_channels = AsyncMock(return_value={"count": 0, "results": []})
        self.client.get_channel_groups = AsyncMock(return_value=[])
        self.client.update_channel = AsyncMock()
        self.client.get_channel_profiles = AsyncMock(
            return_value=[{"id": 1}, {"id": 2}, {"id": 3}]
        )
        self._next_id = iter(range(1000, 2000))
        self.client.create_channel = AsyncMock(
            side_effect=lambda data: {
                "id": next(self._next_id),
                "name": data["name"],
                "channel_number": data.get("channel_number"),
                "channel_group_id": data.get("channel_group_id"),
                "streams": data.get("streams", []),
            }
        )
        self.engine = ChannelPipelineEngine(self.client)
        self._settings_cls = DispatcharrSettings

    def _settings(self, **overrides):
        return self._settings_cls(**overrides)

    def _make_rule(self, actions):
        from models import ChannelPipelineRule

        rule = ChannelPipelineRule()
        rule.id = 1
        rule.name = "R"
        rule.priority = 0
        rule.enabled = True
        rule.m3u_account_id = None
        rule.target_group_id = None
        rule.stop_on_first_match = True
        rule.match_scope_target_group = False
        rule.match_scope_group_id = None
        rule.skip_struck_streams = False
        rule.set_conditions([{"type": "always"}])
        rule.set_actions(actions)
        return rule

    def _run(self, rule, streams, settings, dry_run=False):
        self.engine._load_rules = AsyncMock(return_value=[rule])
        self.engine._fetch_streams = AsyncMock(return_value=streams)
        self.exec_mock = MagicMock(id=1, mode="execute")
        self.engine._create_execution = AsyncMock(return_value=self.exec_mock)
        self.engine._save_execution = AsyncMock()
        self.engine._update_rule_stats = AsyncMock()
        with patch("channel_pipeline_engine.get_session") as mock_get_session, \
                patch("channel_pipeline_engine.get_settings", return_value=settings), \
                patch("channel_pipeline_executor.journal.log_entries"):
            mock_get_session.return_value = MagicMock()
            return asyncio.get_event_loop().run_until_complete(
                self.engine.run_pipeline(dry_run=dry_run)
            )

    def _persisted_warnings(self):
        # exec_mock.set_warnings(list) — capture the list persisted.
        assert self.exec_mock.set_warnings.call_args is not None
        return self.exec_mock.set_warnings.call_args.args[0]

    # -- Finding 1 reversal: default-profile assignment failure escalates -----
    def test_default_profile_failure_finalizes_completed_with_errors(self):
        """A create_channel run with a configured GLOBAL default profile whose
        write fails must NOT finalize green — the default-profile failure now
        escalates through aggregation (the 0152 code left it log-only)."""
        def _update_profile(pid, channel_id, body):
            if pid == 2:
                raise RuntimeError("default profile 2 patch failed")
            return None
        self.client.update_profile_channel = AsyncMock(side_effect=_update_profile)
        rule = self._make_rule([
            {"type": "create_channel", "name_template": "{stream_name}"},
        ])
        streams = [StreamContext(stream_id=1, stream_name="ESPN", m3u_account_id=1)]

        result = self._run(
            rule, streams,
            self._settings(default_channel_profile_ids=[1]),
        )

        assert self.exec_mock.status == "completed_with_errors"
        assert result["success"] is False
        assert result["failed_action_count"] >= 1
        assert any(
            fa.get("action_type") == "assign_default_profile"
            for fa in result["failed_actions"]
        )

    def test_default_profile_all_writes_succeed_finalizes_completed(self):
        """Control: same default-profile config but every write succeeds =>
        green completed."""
        self.client.update_profile_channel = AsyncMock()  # all succeed
        rule = self._make_rule([
            {"type": "create_channel", "name_template": "{stream_name}"},
        ])
        streams = [StreamContext(stream_id=1, stream_name="ESPN", m3u_account_id=1)]

        result = self._run(
            rule, streams,
            self._settings(default_channel_profile_ids=[1]),
        )

        assert self.exec_mock.status == "completed"
        assert result["success"] is True

    # -- Finding 3: non-reversible profile mutation disclosure ----------------
    def test_successful_profile_assignment_persists_non_reversible_warning(self):
        """A successful assign_channel_profile mutates membership non-reversibly,
        so the run persists a ``non_reversible_profile_changes`` warning the UI
        reads to disclose that rollback/undo won't restore it. The run itself
        stays green (the assignment SUCCEEDED)."""
        self.client.update_profile_channel = AsyncMock()  # all succeed
        rule = self._make_rule([
            {"type": "create_channel", "name_template": "{stream_name}"},
            {"type": "assign_channel_profile", "channel_profile_ids": [1]},
        ])
        streams = [StreamContext(stream_id=1, stream_name="ESPN", m3u_account_id=1)]

        result = self._run(rule, streams, self._settings())

        assert self.exec_mock.status == "completed"
        assert result["success"] is True
        warnings = self._persisted_warnings()
        assert any(
            isinstance(w, dict)
            and w.get("type") == "non_reversible_profile_changes"
            and w.get("count", 0) >= 1
            for w in warnings
        )
        # The transient set never rides the serialized result.
        assert "non_reversible_channel_ids" not in result

    def test_no_profile_action_persists_no_non_reversible_warning(self):
        """Control: a run with no profile mutation persists no such warning."""
        self.client.update_profile_channel = AsyncMock()
        rule = self._make_rule([
            {"type": "create_channel", "name_template": "{stream_name}"},
        ])
        streams = [StreamContext(stream_id=1, stream_name="ESPN", m3u_account_id=1)]

        self._run(rule, streams, self._settings())

        warnings = self._persisted_warnings()
        assert not any(
            isinstance(w, dict)
            and w.get("type") == "non_reversible_profile_changes"
            for w in warnings
        )

    # -- GH #720 Part B (4b): ownership-marker-write failure disclosure --------
    def test_marker_write_failure_persists_warning_run_stays_completed(self):
        """Judgment 4b: the profiles applied but the ownership marker write
        FAILED — the run persists a ``profile_ownership_not_established`` warning
        AND stays ``completed`` (NOT completed_with_errors — the assignment
        itself succeeded, so we do not misattribute a successful assign as a
        failed run)."""
        self.client.update_profile_channel = AsyncMock()  # profiles apply
        # The ONLY custom_properties PATCH in this rule is the ownership marker;
        # make exactly that write fail.
        async def _update_channel(cid, data):
            if isinstance(data, dict) and "custom_properties" in data:
                raise RuntimeError("marker write boom")
            return {"id": cid, **(data or {})}
        self.client.update_channel = AsyncMock(side_effect=_update_channel)

        rule = self._make_rule([
            {"type": "create_channel", "name_template": "{stream_name}"},
            {"type": "assign_channel_profile", "channel_profile_ids": [1]},
        ])
        streams = [StreamContext(stream_id=1, stream_name="ESPN", m3u_account_id=1)]

        result = self._run(rule, streams, self._settings())

        assert self.exec_mock.status == "completed"   # NOT completed_with_errors
        assert result["success"] is True
        warnings = self._persisted_warnings()
        assert any(
            isinstance(w, dict)
            and w.get("type") == "profile_ownership_not_established"
            and w.get("count", 0) >= 1
            for w in warnings
        )
        # The transient set never rides the serialized result.
        assert "profile_ownership_unestablished_channel_ids" not in result

    # -- Finding 2: compound capped + failed-action run -----------------------
    def test_capped_and_failed_persists_both_conditions(self):
        """A run that is BOTH capped AND has a failed action persists BOTH in
        the row: status=capped (precedence) but error_message carries the cap
        info AND the failed-action summary — and the result is still
        non-success."""
        def _update_profile(pid, channel_id, body):
            if pid == 2:
                raise RuntimeError("profile 2 patch failed")
            return None
        self.client.update_profile_channel = AsyncMock(side_effect=_update_profile)
        rule = self._make_rule([
            {"type": "create_channel", "name_template": "{stream_name}"},
            {"type": "assign_channel_profile", "channel_profile_ids": [1]},
        ])
        # Two streams, cap of 1: first creates + fails profile assign, second is
        # capped => capped True AND failed_actions non-empty.
        streams = [
            StreamContext(stream_id=1, stream_name="ESPN", m3u_account_id=1),
            StreamContext(stream_id=2, stream_name="FS1", m3u_account_id=1),
        ]

        result = self._run(
            rule, streams,
            self._settings(max_auto_created_channels_per_run=1),
        )

        assert self.exec_mock.status == "capped"
        assert result["success"] is False
        assert result["failed_action_count"] >= 1
        msg = (self.exec_mock.error_message or "").lower()
        # BOTH conditions present in the persisted row.
        assert "cap" in msg
        assert "failed" in msg and "retry" in msg


class TestEventSyncDummyEpgFailureAggregation:
    """y3m6o.1 review (Finding 1): a failed event_sync dummy-EPG assignment must
    aggregate into results['failed_actions'] so the run finalizes
    completed_with_errors. Drives the real ``_assign_event_sync_dummy_epg``
    wrapper with a mocked executor step reporting failures."""

    def _run(self, failed_count):
        client = MagicMock()
        engine = ChannelPipelineEngine(client)

        rule = MagicMock(id=7)
        rule.name = "Dummy EPG Rule"
        config = {"dummy_epg_profile_id": 3}

        epg_summary = {
            "source_id": 55,
            "assign_entries": [],
            "assigned": 0,
            "deferred": 0,
            "already_assigned": 0,
            "skipped_foreign_epg": 0,
            "failed": failed_count,
        }
        exec_ctx = ExecutionContext(dry_run=False)
        executor = MagicMock()
        executor.assign_event_sync_dummy_epg = AsyncMock(return_value=epg_summary)

        results = {
            "event_sync_warnings": [],
            "modified_entities": [],
            "channels_updated": 0,
            "execution_log": [],
            "dry_run_results": [],
        }

        profile = MagicMock(enabled=True)
        profile.name = "Guide"
        sess = MagicMock()
        sess.get.return_value = profile
        with patch("channel_pipeline_engine.get_session", return_value=sess):
            asyncio.get_event_loop().run_until_complete(
                engine._assign_event_sync_dummy_epg(
                    rule, config, executor, exec_ctx, results, dry_run=False
                )
            )
        return results

    def test_failed_dummy_epg_aggregates(self):
        results = self._run(failed_count=2)
        failed = results.get("failed_actions", [])
        assert any(fa["action_type"] == "event_sync_dummy_epg" for fa in failed)
        assert len(failed) == 2

    def test_clean_dummy_epg_aggregates_nothing(self):
        results = self._run(failed_count=0)
        assert not results.get("failed_actions")


class TestPass5DeferredEpgRetryFailureAggregation:
    """y3m6o.1 review (Finding 1): a deferred assign_epg that STILL fails on the
    Pass 5 retry (after the dummy-EPG refresh) must aggregate into
    results['failed_actions'] so the run finalizes completed_with_errors, not
    green. Drives the real ``_refresh_dummy_epg_and_retry`` retry loop with a
    failing ``_execute_assign_epg``."""

    def _run_pass5(self, retry_result, *, source_refresh=None, query_raises=False):
        from channel_pipeline_executor import ActionResult  # noqa: F401

        client = MagicMock()
        client.get_epg_data = AsyncMock(return_value=[])
        engine = ChannelPipelineEngine(client)

        action = MagicMock()
        action.type = "assign_epg"
        action.params = {"epg_id": 5}
        action.to_dict.return_value = {"type": "assign_epg", "epg_id": 5}

        stream_ctx = StreamContext(stream_id=42, stream_name="ESPN", m3u_account_id=1)

        executor = MagicMock()
        executor._deferred_epg_assignments = [(100, action, stream_ctx, MagicMock())]
        executor._channel_by_id = {100: {"name": "ESPN", "channel_group_id": 9}}
        executor._group_by_id = {9: {"name": "Sports"}}
        executor.reload_epg_data = MagicMock()
        executor._execute_assign_epg = AsyncMock(return_value=retry_result)

        epg_sources = [{"id": 5, "name": "Dummy", "url": "/api/dummy-epg/xmltv/1"}]
        results = {"execution_log": [], "dry_run_results": []}

        fake_task = MagicMock()
        fake_task._regenerate_xmltv = AsyncMock(return_value=1)
        sess = MagicMock()
        if query_raises:
            # WARN #2: Step 1 profile-group update raises.
            sess.query.side_effect = RuntimeError("profile group update boom")
        else:
            sess.query.return_value.filter.return_value.all.return_value = []
        refresh_mock = (
            AsyncMock(side_effect=source_refresh) if callable(source_refresh)
            else AsyncMock()
        )
        with patch("channel_pipeline_engine.get_session", return_value=sess), \
                patch("database.get_session", return_value=sess), \
                patch("tasks.dummy_epg_refresh.DummyEPGRefreshTask",
                      return_value=fake_task), \
                patch("tasks.dummy_epg_refresh.wait_for_epg_source_refresh",
                      new=refresh_mock):
            asyncio.get_event_loop().run_until_complete(
                engine._refresh_dummy_epg_and_retry(
                    executor, results, epg_sources, dry_run=False
                )
            )
        return results

    @staticmethod
    def _log_entries(results, type_):
        return [
            a for e in results["execution_log"]
            for a in e["actions_executed"] if a["type"] == type_
        ]

    def test_failed_retry_aggregates(self):
        from channel_pipeline_executor import ActionResult

        failing = ActionResult(
            success=False, action_type="assign_epg",
            description="assign_epg still failing after refresh",
            entity_id=100, error="dispatcharr rejected",
        )
        results = self._run_pass5(failing)
        failed = results.get("failed_actions", [])
        assert any(fa["action_type"] == "assign_epg" for fa in failed)
        assert len(failed) == 1

    def test_successful_retry_aggregates_nothing(self):
        from channel_pipeline_executor import ActionResult

        ok = ActionResult(
            success=True, action_type="assign_epg",
            description="assigned", entity_id=100,
        )
        results = self._run_pass5(ok)
        assert not results.get("failed_actions")

    def _ok_retry(self):
        from channel_pipeline_executor import ActionResult
        return ActionResult(
            success=True, action_type="assign_epg", description="assigned",
            entity_id=100,
        )

    def test_profile_group_update_failure_aggregates(self):
        """y3m6o.1 review (WARN #2): a Pass 5 profile-group update failure now
        escalates so the run finalizes completed_with_errors, not green."""
        results = self._run_pass5(self._ok_retry(), query_raises=True)
        failed = results.get("failed_actions", [])
        assert any(fa["action_type"] == "dummy_epg_refresh" for fa in failed)

    def test_source_refresh_failure_aggregates_and_logs_honestly(self):
        """y3m6o.1 review (WARN #3): a Pass 5 source-refresh failure escalates
        AND its execution-log entry reflects the actual failure (previously it
        recorded success=True even when the refresh raised)."""
        def _boom(*a, **k):
            raise RuntimeError("refresh timed out")
        results = self._run_pass5(self._ok_retry(), source_refresh=_boom)

        # Escalated into aggregation.
        failed = results.get("failed_actions", [])
        assert any(fa["action_type"] == "refresh_epg_source" for fa in failed)
        # And the execution-log entry is honest (success=False + error).
        entries = self._log_entries(results, "refresh_epg_source")
        assert entries and entries[0]["success"] is False
        assert entries[0]["error"] is not None
        assert "Failed to refresh" in entries[0]["description"]

    def test_source_refresh_success_logs_success_and_no_failure(self):
        """Control: a healthy source refresh logs success=True and aggregates
        nothing."""
        results = self._run_pass5(self._ok_retry())
        entries = self._log_entries(results, "refresh_epg_source")
        assert entries and entries[0]["success"] is True
        assert entries[0]["error"] is None
        assert not any(
            fa["action_type"] == "refresh_epg_source"
            for fa in results.get("failed_actions", [])
        )


class TestEngineFoldMatchKeyPassThrough:
    """GH #645 / bead enhancedchannelmanager-0vao3: the engine must thread the
    rule's ``fold_match_key`` flag into every executor.execute call, the same
    way the sibling per-rule flags are threaded."""

    def setup_method(self):
        self.client = MagicMock()
        self.client.get_channels = AsyncMock(return_value={"count": 0, "results": []})
        self.engine = ChannelPipelineEngine(self.client)
        self.engine._existing_channels = []
        self.engine._existing_groups = []

    @patch("channel_pipeline_engine.get_session")
    def test_rule_flag_reaches_executor(self, mock_get_session):
        from channel_pipeline_executor import ActionResult

        mock_get_session.return_value = MagicMock()

        rule = MagicMock()
        rule.id = 1
        rule.name = "Folded Rule"
        rule.priority = 0
        rule.m3u_account_id = None
        rule.target_group_id = None
        rule.enabled = True
        rule.stop_on_first_match = True
        rule.skip_struck_streams = False
        rule.sort_field = None
        rule.sort_order = "asc"
        rule.sort_regex = None
        rule.orphan_action = "none"
        rule.managed_channel_ids = None
        rule.get_managed_channel_ids.return_value = []
        rule.get_conditions.return_value = [{"type": "always"}]
        rule.get_actions.return_value = [
            {"type": "create_channel", "name_template": "{stream_name}",
             "if_exists": "merge"}
        ]
        rule.get_normalization_group_ids.return_value = []
        rule.match_scope_target_group = False
        rule.match_scope_group_id = None
        rule.allow_manual_channel_merge = False
        rule.fold_match_key = True

        streams = [StreamContext(stream_id=101, stream_name="Eurosport 2",
                                 m3u_account_id=1, m3u_account_name="P")]

        mock_execution = MagicMock()
        mock_execution.id = 1

        with patch("channel_pipeline_engine.ActionExecutor") as mock_exec_cls:
            mock_executor = MagicMock()
            mock_executor.execute = AsyncMock(return_value=ActionResult(
                success=True, action_type="create_channel", description="ok",
                entity_type="channel", entity_id=201, entity_name="Eurosport 2",
                created=True,
            ))
            mock_executor.verify_epg_assignments = AsyncMock(return_value=(0, 0, 0))
            mock_executor.prune_merge_streams = AsyncMock()
            mock_executor.reorder_streams_on_channels = AsyncMock(return_value=0)
            mock_executor._channel_by_id = {}
            mock_executor._created_channels = {}
            mock_exec_cls.return_value = mock_executor

            self.engine._refresh_dummy_epg_and_retry = AsyncMock()
            self.engine._reconcile_orphans = AsyncMock()
            self.engine._update_rule_stats = AsyncMock()

            asyncio.get_event_loop().run_until_complete(
                self.engine._process_streams(streams, [rule], mock_execution, dry_run=False)
            )

        assert mock_executor.execute.await_count == 1
        assert mock_executor.execute.await_args.kwargs.get("fold_match_key") is True


class TestAutoChannelNumberSkipsRenumberPass:
    """Bead enhancedchannelmanager-bn0wa: pin the CURRENT intentional behavior
    that a create_channel action with channel_number "auto" yields
    ``_get_rule_starting_number(rule) is None`` and therefore SKIPS the
    rule-level renumber pass (the ``continue`` in Pass 3). This footgun was
    previously unguarded — if a refactor changes it in either direction,
    these tests fail.
    """

    def test_auto_spec_returns_none(self):
        from channel_pipeline_engine import _get_rule_starting_number

        rule = MagicMock()
        rule.get_actions.return_value = [
            {"type": "create_channel", "channel_number": "auto"}
        ]
        assert _get_rule_starting_number(rule) is None

    def test_missing_spec_defaults_to_auto_and_returns_none(self):
        from channel_pipeline_engine import _get_rule_starting_number

        rule = MagicMock()
        rule.get_actions.return_value = [{"type": "create_channel"}]
        assert _get_rule_starting_number(rule) is None

    def test_int_spec_returns_int(self):
        from channel_pipeline_engine import _get_rule_starting_number

        rule = MagicMock()
        rule.get_actions.return_value = [
            {"type": "create_channel", "channel_number": 500}
        ]
        assert _get_rule_starting_number(rule) == 500

    def test_range_spec_returns_range_start(self):
        from channel_pipeline_engine import _get_rule_starting_number

        rule = MagicMock()
        rule.get_actions.return_value = [
            {"type": "create_channel", "channel_number": "500-999"}
        ]
        assert _get_rule_starting_number(rule) == 500

    def test_numeric_string_spec_returns_int(self):
        from channel_pipeline_engine import _get_rule_starting_number

        rule = MagicMock()
        rule.get_actions.return_value = [
            {"type": "create_channel", "channel_number": "500"}
        ]
        assert _get_rule_starting_number(rule) == 500

    def test_garbage_spec_returns_none(self):
        from channel_pipeline_engine import _get_rule_starting_number

        rule = MagicMock()
        rule.get_actions.return_value = [
            {"type": "create_channel", "channel_number": "not-a-number"}
        ]
        assert _get_rule_starting_number(rule) is None

    def test_no_create_channel_action_returns_none(self):
        from channel_pipeline_engine import _get_rule_starting_number

        rule = MagicMock()
        rule.get_actions.return_value = [{"type": "merge_streams", "target": "auto"}]
        assert _get_rule_starting_number(rule) is None

    @patch("channel_pipeline_engine.get_session")
    def test_pass3_renumber_skipped_for_auto_numbered_rule(self, mock_get_session):
        """Behavioral pin of the Pass 3 ``continue``: a rule WITH sort_field
        that creates channels but numbers them "auto" must never call
        assign_channel_numbers (rule-level renumbering is skipped entirely)."""
        from channel_pipeline_executor import ActionResult

        mock_get_session.return_value = MagicMock()

        client = MagicMock()
        client.assign_channel_numbers = AsyncMock()
        client.get_channels = AsyncMock(return_value={"count": 0, "results": []})
        engine = ChannelPipelineEngine(client)
        engine._existing_channels = []
        engine._existing_groups = []

        rule = MagicMock()
        rule.id = 1
        rule.name = "Auto Numbered Rule"
        rule.priority = 0
        rule.m3u_account_id = None
        rule.target_group_id = None
        rule.enabled = True
        rule.stop_on_first_match = True
        rule.skip_struck_streams = False
        rule.sort_field = "name"  # renumber pass is otherwise eligible
        rule.sort_order = "asc"
        rule.sort_regex = None
        rule.orphan_action = "none"
        rule.managed_channel_ids = None
        rule.get_managed_channel_ids.return_value = []
        rule.get_conditions.return_value = [{"type": "always"}]
        rule.get_actions.return_value = [
            {"type": "create_channel", "name_template": "{stream_name}",
             "channel_number": "auto"}
        ]
        rule.get_normalization_group_ids.return_value = []
        rule.match_scope_target_group = False
        rule.match_scope_group_id = None
        rule.allow_manual_channel_merge = False
        rule.fold_match_key = False

        streams = [
            StreamContext(stream_id=101, stream_name="ESPN A", m3u_account_id=1, m3u_account_name="P"),
            StreamContext(stream_id=102, stream_name="ESPN B", m3u_account_id=1, m3u_account_name="P"),
        ]

        created_ids = iter([201, 202])

        async def _fake_execute(action, stream_ctx, exec_ctx, *args, **kwargs):
            cid = next(created_ids)
            exec_ctx.current_channel_id = cid
            exec_ctx.created_channel_ids.add(cid)
            exec_ctx.channels_created += 1
            return ActionResult(
                success=True, action_type="create_channel", description="created",
                entity_type="channel", entity_id=cid, entity_name=f"ch-{cid}",
                created=True,
            )

        mock_execution = MagicMock()
        mock_execution.id = 1

        with patch("channel_pipeline_engine.ActionExecutor") as mock_exec_cls:
            mock_executor = MagicMock()
            mock_executor.execute = AsyncMock(side_effect=_fake_execute)
            mock_executor.verify_epg_assignments = AsyncMock(return_value=(0, 0, 0))
            mock_executor.prune_merge_streams = AsyncMock()
            mock_executor.reorder_streams_on_channels = AsyncMock(return_value=0)
            mock_executor._channel_by_id = {}
            mock_executor._created_channels = {}
            mock_exec_cls.return_value = mock_executor

            engine._refresh_dummy_epg_and_retry = AsyncMock()
            engine._reconcile_orphans = AsyncMock()
            engine._update_rule_stats = AsyncMock()

            asyncio.get_event_loop().run_until_complete(
                engine._process_streams(streams, [rule], mock_execution, dry_run=False)
            )

        assert client.assign_channel_numbers.await_count == 0, (
            "channel_number 'auto' must skip the rule-level renumber pass "
            "(bead bn0wa pins this intentional behavior)"
        )


class TestProfileFetchGate:
    """GH #720 / y3m6o — the load-bearing half of the fix.

    The bug was that the channel-profile universe was never *fetched* (empty
    list) when a rule used ``assign_channel_profile`` but no global
    ``default_channel_profile_ids`` was set — the executor then correctly
    degrades to enable-only, reproducing the bug. These tests pin the
    ``needs_profiles`` gate in ``_process_streams`` (channel_pipeline_engine):
    ``get_channel_profiles`` MUST be called when a rule carries an
    ``assign_channel_profile`` action even with no global default, and MUST
    NOT be called when neither is present.

    Real ``ChannelPipelineRule`` objects are used (not MagicMock rules) so the
    real ``get_actions()`` JSON-parse and the gate's dict-shape branch are
    genuinely exercised. Streams are empty so no action is ever *executed* —
    the test isolates the fetch decision, which happens before Pass 1.
    """

    def _make_engine(self, get_channel_profiles):
        client = MagicMock()
        client.get_channels = AsyncMock(return_value={"count": 0, "results": []})
        client.get_channel_profiles = get_channel_profiles
        engine = ChannelPipelineEngine(client)
        engine._existing_channels = []
        engine._existing_groups = []
        # Downstream passes are irrelevant to the fetch decision; stub the
        # async collaborators so the empty-stream run completes cleanly.
        engine._reconcile_orphans = AsyncMock()
        engine._update_rule_stats = AsyncMock()
        engine._refresh_dummy_epg_and_retry = AsyncMock()
        return client, engine

    @staticmethod
    def _rule(actions):
        from models import ChannelPipelineRule
        return ChannelPipelineRule(
            name="Profile Rule", enabled=True, priority=0,
            conditions=json.dumps([{"type": "always"}]),
            actions=json.dumps(actions),
        )

    @patch("channel_pipeline_engine.get_session")
    def test_fetches_profiles_for_assign_channel_profile_rule_without_global_default(
        self, mock_get_session
    ):
        """A rule with an assign_channel_profile action forces the profile
        fetch even when no global default is configured (the #720 regression
        half)."""
        from config import get_settings
        # Precondition: no global default — the action is the ONLY reason to fetch.
        assert not get_settings().default_channel_profile_ids

        mock_get_session.return_value = MagicMock()
        get_profiles = AsyncMock(return_value=[{"id": 1}, {"id": 2}, {"id": 3}])
        client, engine = self._make_engine(get_profiles)

        rule = self._rule(
            [{"type": "assign_channel_profile", "channel_profile_ids": [1]}]
        )
        mock_execution = MagicMock()
        mock_execution.id = 1

        asyncio.get_event_loop().run_until_complete(
            engine._process_streams([], [rule], mock_execution, dry_run=True)
        )

        get_profiles.assert_awaited_once()

    @patch("channel_pipeline_engine.get_session")
    def test_does_not_fetch_profiles_without_default_or_assign_action(
        self, mock_get_session
    ):
        """No global default and no assign_channel_profile action => the
        profile universe is never fetched."""
        from config import get_settings
        assert not get_settings().default_channel_profile_ids

        mock_get_session.return_value = MagicMock()
        get_profiles = AsyncMock(return_value=[{"id": 1}, {"id": 2}])
        client, engine = self._make_engine(get_profiles)

        rule = self._rule(
            [{"type": "create_channel", "name_template": "{stream_name}"}]
        )
        mock_execution = MagicMock()
        mock_execution.id = 1

        asyncio.get_event_loop().run_until_complete(
            engine._process_streams([], [rule], mock_execution, dry_run=True)
        )

        get_profiles.assert_not_called()


class TestProfileUniverseSentinelBehavioral:
    """GH #720 / y3m6o.1 — behavioral JOIN of the engine fetch-gate and the
    executor's exclusive-membership half.

    These tests do NOT inject ``all_profile_ids`` directly. They drive the real
    ``_process_streams`` fetch-gate (which fetches the profile universe, or on
    failure produces the ``None`` "unavailable" sentinel) and capture the REAL
    ``ActionExecutor`` the engine constructs from that outcome, then exercise
    that same executor's ``assign_channel_profile`` path. This proves the two
    halves are wired together: a fetch failure at the engine layer makes the
    executor's assign action FAIL rather than silently enable-only-and-succeed.
    """

    def _make_engine(self, get_channel_profiles):
        client = MagicMock()
        client.get_channels = AsyncMock(return_value={"count": 0, "results": []})
        client.get_channel_profiles = get_channel_profiles
        client.update_profile_channel = AsyncMock()
        engine = ChannelPipelineEngine(client)
        engine._existing_channels = []
        engine._existing_groups = []
        engine._reconcile_orphans = AsyncMock()
        engine._update_rule_stats = AsyncMock()
        engine._refresh_dummy_epg_and_retry = AsyncMock()
        engine._run_event_sync_rules = AsyncMock()
        engine._reorder_channel_streams = AsyncMock()
        return client, engine

    @staticmethod
    def _standard_rule(actions):
        from models import ChannelPipelineRule
        return ChannelPipelineRule(
            name="Profile Rule", enabled=True, priority=0,
            conditions=json.dumps([{"type": "always"}]),
            actions=json.dumps(actions),
        )

    @staticmethod
    def _event_rule(actions, rule_id=9):
        """An event_sync-kind rule carrying regular actions (Part A's gate
        iterates event_sync rules' actions too)."""
        rule = MagicMock()
        rule.id = rule_id
        rule.name = "Event Profile Rule"
        rule.priority = 0
        rule.enabled = True
        rule.is_event_sync.return_value = True
        rule.get_actions.return_value = list(actions)
        rule.get_normalization_group_ids.return_value = []
        rule.get_conditions.return_value = [{"type": "always"}]
        rule.get_event_sync_config.return_value = {
            "master_group_id": 10, "secondary_group_ids": [20], "enabled": True,
        }
        rule.stream_sort_field = None
        return rule

    def _run_and_capture_executor(self, engine, *, rules=None, event_sync_rules=None):
        """Run ``_process_streams`` with empty streams and capture the real
        ActionExecutor the engine builds (with the real profile-universe
        outcome plumbed in)."""
        from channel_pipeline_executor import ActionExecutor as RealActionExecutor
        captured = {}

        def _spy(*args, **kwargs):
            inst = RealActionExecutor(*args, **kwargs)
            captured["executor"] = inst
            captured["all_profile_ids"] = kwargs.get("all_profile_ids", "MISSING")
            captured["channel_profile_membership"] = kwargs.get(
                "channel_profile_membership", "MISSING"
            )
            return inst

        mock_execution = MagicMock()
        mock_execution.id = 1
        with patch("channel_pipeline_engine.get_session", return_value=MagicMock()), \
             patch("channel_pipeline_engine.ActionExecutor", side_effect=_spy):
            asyncio.get_event_loop().run_until_complete(
                engine._process_streams(
                    [], rules or [], mock_execution, dry_run=False,
                    event_sync_rules=event_sync_rules,
                )
            )
        return captured

    @staticmethod
    def _assign(executor, channel_id=99, selected=(1,)):
        stream = StreamContext(
            stream_id=1, stream_name="ESPN", m3u_account_id=1, m3u_account_name="P",
        )
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = channel_id
        action = {"type": "assign_channel_profile",
                  "channel_profile_ids": list(selected)}
        return asyncio.get_event_loop().run_until_complete(
            executor.execute(action, stream, exec_ctx)
        )

    def test_standard_rule_fetch_failure_fails_assign_action(self):
        """Acceptance (a): a standard rule with assign_channel_profile — when the
        profile-universe fetch RAISES, the engine hands the executor the None
        sentinel and the executor's assign action FAILS (not enable-only)."""
        get_profiles = AsyncMock(side_effect=RuntimeError("dispatcharr down"))
        client, engine = self._make_engine(get_profiles)
        rule = self._standard_rule(
            [{"type": "assign_channel_profile", "channel_profile_ids": [1]}]
        )

        captured = self._run_and_capture_executor(engine, rules=[rule])

        get_profiles.assert_awaited_once()
        assert captured["all_profile_ids"] is None  # engine produced the sentinel
        result = self._assign(captured["executor"])
        assert result.success is False
        client.update_profile_channel.assert_not_called()

    def test_standard_rule_fetch_success_assign_enforces_exclusivity(self):
        """Contrast: a healthy fetch => the executor performs real exclusive
        membership and reports success. Channel 99 is created this run, so
        Dispatcharr auto-joined it to ALL profiles; the diff-aware reconcile
        DISABLES the unselected profiles (2, 3) and skips the redundant enable of
        the already-joined selected profile (1) — end state = exactly {1}
        (y3m6o.1 review follow-up: no phantom enable write)."""
        get_profiles = AsyncMock(return_value=[{"id": 1}, {"id": 2}, {"id": 3}])
        client, engine = self._make_engine(get_profiles)
        rule = self._standard_rule(
            [{"type": "assign_channel_profile", "channel_profile_ids": [1]}]
        )

        captured = self._run_and_capture_executor(engine, rules=[rule])

        assert captured["all_profile_ids"] == [1, 2, 3]
        result = self._assign(captured["executor"], selected=(1,))
        assert result.success is True
        calls = {c.args[0]: c.args[2]["enabled"]
                 for c in client.update_profile_channel.call_args_list}
        # Only the unselected profiles flip (disable); enable-1 is a no-op.
        assert calls == {2: False, 3: False}

    def test_event_sync_rule_assign_fetches_and_enforces_exclusivity(self):
        """Acceptance (b): an EVENT-SYNC rule carrying assign_channel_profile
        forces the profile fetch (Part A's gate honors both paths) AND the real
        executor honors exclusive membership for it."""
        get_profiles = AsyncMock(return_value=[{"id": 1}, {"id": 2}, {"id": 3}])
        client, engine = self._make_engine(get_profiles)
        rule = self._event_rule(
            [{"type": "assign_channel_profile", "channel_profile_ids": [2]}]
        )

        captured = self._run_and_capture_executor(engine, event_sync_rules=[rule])

        get_profiles.assert_awaited_once()  # event-sync path honored by the gate
        assert captured["all_profile_ids"] == [1, 2, 3]
        result = self._assign(captured["executor"], selected=(2,))
        assert result.success is True
        calls = {c.args[0]: c.args[2]["enabled"]
                 for c in client.update_profile_channel.call_args_list}
        # Channel 99 auto-joined to all; only the unselected profiles flip
        # (disable 1, 3), enable-2 is a redundant no-op that is skipped.
        assert calls == {1: False, 3: False}

    def test_engine_builds_membership_map_from_profile_channels(self):
        """y3m6o.1 review follow-up: the engine inverts the profile payload's
        ``channels`` (enabled member ids) into a channel_id -> {profile_ids} map
        and threads it to the executor, so assign_channel_profile can diff and
        skip no-op writes. Zero extra API reads (same get_channel_profiles fetch)."""
        get_profiles = AsyncMock(return_value=[
            {"id": 1, "channels": [50, 51]},
            {"id": 2, "channels": [51]},
            {"id": 3, "channels": []},
        ])
        client, engine = self._make_engine(get_profiles)
        rule = self._standard_rule(
            [{"type": "assign_channel_profile", "channel_profile_ids": [1]}]
        )

        captured = self._run_and_capture_executor(engine, rules=[rule])

        assert captured["all_profile_ids"] == [1, 2, 3]
        assert captured["channel_profile_membership"] == {50: {1}, 51: {1, 2}}

    def test_event_sync_rule_fetch_failure_fails_assign_action(self):
        """Event-sync path, fetch failure => None sentinel => assign FAILS,
        never silently enable-only."""
        get_profiles = AsyncMock(side_effect=RuntimeError("dispatcharr down"))
        client, engine = self._make_engine(get_profiles)
        rule = self._event_rule(
            [{"type": "assign_channel_profile", "channel_profile_ids": [2]}]
        )

        captured = self._run_and_capture_executor(engine, event_sync_rules=[rule])

        get_profiles.assert_awaited_once()
        assert captured["all_profile_ids"] is None
        result = self._assign(captured["executor"], selected=(2,))
        assert result.success is False
        client.update_profile_channel.assert_not_called()
