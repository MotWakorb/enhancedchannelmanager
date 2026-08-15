"""
Unit tests for the auto-creation executor service.

Tests the ActionExecutor class which executes actions against channels, groups,
and streams with proper rollback tracking.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock, patch
import asyncio
import pytest

from channel_number import is_valid_channel_number
from channel_pipeline_executor import (
    ActionResult,
    ExecutionContext,
    ActionExecutor,
)
from channel_pipeline_evaluator import ConditionEvaluator, StreamContext


class TestActionResult:
    """Tests for ActionResult dataclass."""

    def test_default_values(self):
        """ActionResult has sensible defaults."""
        result = ActionResult(
            success=True,
            action_type="create_channel",
            description="Test"
        )
        assert result.success is True
        assert result.entity_type is None
        assert result.entity_id is None
        assert result.created is False
        assert result.modified is False
        assert result.skipped is False
        assert result.previous_state is None
        assert result.error is None

    def test_full_result(self):
        """ActionResult with all fields."""
        result = ActionResult(
            success=True,
            action_type="create_channel",
            description="Created ESPN",
            entity_type="channel",
            entity_id=42,
            entity_name="ESPN",
            created=True,
            modified=False,
            skipped=False,
            previous_state=None,
            error=None
        )
        assert result.entity_id == 42
        assert result.entity_name == "ESPN"
        assert result.created is True

    def test_error_result(self):
        """ActionResult for failed action."""
        result = ActionResult(
            success=False,
            action_type="create_channel",
            description="Failed to create channel",
            error="API connection failed"
        )
        assert result.success is False
        assert result.error == "API connection failed"


class TestExecutionContext:
    """Tests for ExecutionContext dataclass."""

    def test_default_values(self):
        """ExecutionContext has sensible defaults."""
        ctx = ExecutionContext()
        assert ctx.dry_run is False
        assert ctx.results == []
        assert ctx.created_entities == []
        assert ctx.modified_entities == []
        assert ctx.channels_created == 0
        assert ctx.channels_updated == 0
        assert ctx.groups_created == 0
        assert ctx.streams_merged == 0
        assert ctx.streams_skipped == 0
        assert ctx.current_channel_id is None
        assert ctx.current_group_id is None

    def test_dry_run_mode(self):
        """ExecutionContext in dry run mode."""
        ctx = ExecutionContext(dry_run=True)
        assert ctx.dry_run is True

    def test_add_result_channel_created(self):
        """add_result tracks created channels."""
        ctx = ExecutionContext()
        result = ActionResult(
            success=True,
            action_type="create_channel",
            description="Created ESPN",
            entity_type="channel",
            entity_id=1,
            entity_name="ESPN",
            created=True
        )
        ctx.add_result(result)

        assert len(ctx.results) == 1
        assert ctx.channels_created == 1
        assert len(ctx.created_entities) == 1
        assert ctx.created_entities[0]["type"] == "channel"
        assert ctx.created_entities[0]["id"] == 1

    def test_add_result_tracks_non_reversible_channel(self):
        """y3m6o.1 review (Finding 3): a modified-but-non-rollbackable channel
        result (assign_channel_profile) is recorded on
        non_reversible_channel_ids at the add_result chokepoint and NOT as a
        rollback/modified entity."""
        ctx = ExecutionContext()
        result = ActionResult(
            success=True,
            action_type="assign_channel_profile",
            description="Assigned channel profiles",
            entity_type="channel",
            entity_id=42,
            entity_name="ESPN",
            modified=True,
            rollbackable=False,
        )
        ctx.add_result(result)

        assert ctx.non_reversible_channel_ids == {42}
        # A non-rollbackable change is NOT counted as a reversible modified entity.
        assert ctx.modified_entities == []
        # It is still counted as a channel update.
        assert ctx.channels_updated == 1

    def test_add_result_reversible_channel_not_flagged_non_reversible(self):
        """Control: a normal rollbackable channel modification is a modified
        entity and is NOT flagged non-reversible."""
        ctx = ExecutionContext()
        result = ActionResult(
            success=True,
            action_type="assign_logo",
            description="Assigned logo",
            entity_type="channel",
            entity_id=7,
            entity_name="ESPN",
            modified=True,
            rollbackable=True,
            previous_state={"logo": None},
        )
        ctx.add_result(result)

        assert ctx.non_reversible_channel_ids == set()
        assert len(ctx.modified_entities) == 1

    def test_add_result_group_created(self):
        """add_result tracks created groups."""
        ctx = ExecutionContext()
        result = ActionResult(
            success=True,
            action_type="create_group",
            description="Created Sports",
            entity_type="group",
            entity_id=5,
            entity_name="Sports",
            created=True
        )
        ctx.add_result(result)

        assert ctx.groups_created == 1
        assert ctx.created_entities[0]["type"] == "group"

    def test_add_result_merge_stream_increments_streams_merged_not_channels_updated(self):
        """merge_stream results count as streams_merged, NOT channels_updated (bd-0emgo.4)."""
        ctx = ExecutionContext()
        result = ActionResult(
            success=True,
            action_type="merge_stream",
            description="Added stream to ESPN",
            entity_type="channel",
            entity_id=1,
            entity_name="ESPN",
            modified=True,
            previous_state={"streams": [101]}
        )
        ctx.add_result(result)

        # The fix: merge operations go to streams_merged, not channels_updated.
        assert ctx.streams_merged == 1
        assert ctx.channels_updated == 0
        assert len(ctx.modified_entities) == 1
        assert ctx.modified_entities[0]["previous"]["streams"] == [101]

    def test_add_result_property_update_increments_channels_updated(self):
        """Non-merge channel modifications (logo, tvg, epg, etc.) increment channels_updated (bd-0emgo.4)."""
        ctx = ExecutionContext()
        for action_type in ("update_channel", "assign_logo", "assign_tvg_id", "assign_epg",
                            "assign_profile", "assign_channel_profile", "set_channel_number"):
            result = ActionResult(
                success=True,
                action_type=action_type,
                description=f"Updated channel via {action_type}",
                entity_type="channel",
                entity_id=1,
                entity_name="ESPN",
                modified=True,
            )
            ctx.add_result(result)

        assert ctx.channels_updated == 7
        assert ctx.streams_merged == 0

    def test_add_result_multiple_merges_into_distinct_channels(self):
        """N merge_stream results do NOT inflate channels_updated (bd-0emgo.4 regression guard)."""
        ctx = ExecutionContext()
        # Simulate 1341 merges (the production bug scenario)
        for i in range(1341):
            result = ActionResult(
                success=True,
                action_type="merge_stream",
                description=f"Added stream {i} to channel",
                entity_type="channel",
                entity_id=(i % 726) + 1,  # 726 distinct channels
                entity_name=f"Channel {(i % 726) + 1}",
                modified=True,
            )
            ctx.add_result(result)

        # Old behavior was channels_updated == 1341 (the inflation bug).
        assert ctx.channels_updated == 0, (
            "channels_updated must NOT count merge operations (was the bd-0emgo.4 inflation bug)"
        )
        assert ctx.streams_merged == 1341

    def test_add_result_skipped(self):
        """add_result tracks skipped streams."""
        ctx = ExecutionContext()
        result = ActionResult(
            success=True,
            action_type="skip",
            description="Stream skipped",
            skipped=True
        )
        ctx.add_result(result)

        assert ctx.streams_skipped == 1


class TestActionExecutorInit:
    """Tests for ActionExecutor initialization."""

    def test_init_empty(self):
        """Initialize executor with no channels/groups."""
        client = MagicMock()
        executor = ActionExecutor(client)

        assert executor.client == client
        assert executor.existing_channels == []
        assert executor.existing_groups == []

    def test_init_with_channels(self):
        """Initialize executor with existing channels."""
        client = MagicMock()
        channels = [
            {"id": 1, "name": "ESPN", "channel_number": 100, "auto_created": True},
            {"id": 2, "name": "CNN", "channel_number": 200, "auto_created": True},
        ]
        executor = ActionExecutor(client, existing_channels=channels)

        assert len(executor.existing_channels) == 2
        assert executor._channel_by_id[1]["name"] == "ESPN"
        assert executor._channel_by_name["espn"]["id"] == 1
        assert executor._is_channel_number_used(100)
        assert executor._is_channel_number_used(200)
        assert not executor._is_channel_number_used(101)

    def test_init_with_groups(self):
        """Initialize executor with existing groups."""
        client = MagicMock()
        groups = [
            {"id": 1, "name": "Sports"},
            {"id": 2, "name": "News"},
        ]
        executor = ActionExecutor(client, existing_groups=groups)

        assert len(executor.existing_groups) == 2
        assert executor._group_by_id[1]["name"] == "Sports"
        assert executor._group_by_name["sports"]["id"] == 1


class TestActionExecutorHelpers:
    """Tests for ActionExecutor helper methods."""

    def setup_method(self):
        """Set up test fixtures."""
        self.client = MagicMock()
        self.channels = [
            {"id": 1, "name": "ESPN", "tvg_id": "ESPN.US", "channel_number": 100, "auto_created": True},
            {"id": 2, "name": "ESPN2", "tvg_id": "ESPN2.US", "channel_number": 101, "auto_created": True},
            {"id": 3, "name": "CNN", "tvg_id": "CNN.US", "channel_number": 200, "auto_created": True},
        ]
        self.groups = [
            {"id": 1, "name": "Sports"},
            {"id": 2, "name": "News"},
        ]
        self.executor = ActionExecutor(
            self.client,
            existing_channels=self.channels,
            existing_groups=self.groups
        )

    def test_find_channel_by_name_exact(self):
        """Find channel by exact name."""
        channel = self.executor._find_channel_by_name("ESPN")
        assert channel["id"] == 1

    def test_find_channel_by_name_case_insensitive(self):
        """Find channel by name is case-insensitive."""
        channel = self.executor._find_channel_by_name("espn")
        assert channel["id"] == 1

        channel = self.executor._find_channel_by_name("EsPn")
        assert channel["id"] == 1

    def test_find_channel_by_name_not_found(self):
        """Find channel returns None when not found."""
        channel = self.executor._find_channel_by_name("FOX")
        assert channel is None

    def test_find_channel_by_name_created(self):
        """Find channel finds newly created channels."""
        self.executor._created_channels["fox"] = {"id": 99, "name": "FOX", "auto_created": True}
        channel = self.executor._find_channel_by_name("FOX")
        assert channel["id"] == 99

    def test_find_channel_by_regex(self):
        """Find channel by regex pattern."""
        channel = self.executor._find_channel_by_regex(r"ESPN\d*$")
        assert channel is not None
        assert channel["name"].startswith("ESPN")

    def test_find_channel_by_regex_no_match(self):
        """Find channel by regex returns None for no match."""
        channel = self.executor._find_channel_by_regex(r"^FOX\d+$")
        assert channel is None

    def test_find_channel_by_regex_invalid(self):
        """Find channel by regex handles invalid regex gracefully."""
        channel = self.executor._find_channel_by_regex(r"[invalid(")
        assert channel is None

    def test_find_channel_by_tvg_id(self):
        """Find channel by TVG ID."""
        channel = self.executor._find_channel_by_tvg_id("ESPN.US")
        assert channel["id"] == 1

    def test_find_channel_by_tvg_id_not_found(self):
        """Find channel by TVG ID returns None when not found."""
        channel = self.executor._find_channel_by_tvg_id("FOX.US")
        assert channel is None

    def test_find_channel_by_tvg_id_none(self):
        """Find channel by TVG ID handles None."""
        channel = self.executor._find_channel_by_tvg_id(None)
        assert channel is None

    def test_find_group_by_name(self):
        """Find group by name."""
        group = self.executor._find_group_by_name("Sports")
        assert group["id"] == 1

    def test_find_group_by_name_case_insensitive(self):
        """Find group by name is case-insensitive."""
        group = self.executor._find_group_by_name("SPORTS")
        assert group["id"] == 1

    def test_find_group_by_name_not_found(self):
        """Find group returns None when not found."""
        group = self.executor._find_group_by_name("Movies")
        assert group is None

    def test_get_next_channel_number_auto(self):
        """Get next auto-assigned channel number."""
        # Channel numbers 100, 101, 200 are used
        num = self.executor._get_next_channel_number("auto")
        assert num == 1  # First available

    def test_get_next_channel_number_specific(self):
        """Get specific channel number."""
        num = self.executor._get_next_channel_number(500)
        assert num == 500

    def test_get_next_channel_number_specific_string(self):
        """Get specific channel number from string."""
        num = self.executor._get_next_channel_number("500")
        assert num == 500

    def test_get_next_channel_number_range(self):
        """Get channel number from range."""
        num = self.executor._get_next_channel_number("99-105")
        assert num == 99  # First available in range

    def test_get_next_channel_number_range_skip_used(self):
        """Get channel number from range skips used numbers."""
        num = self.executor._get_next_channel_number("100-105")
        assert num == 102  # 100 and 101 are used


class TestActionExecutorExecute:
    """Tests for ActionExecutor.execute method."""

    def setup_method(self):
        """Set up test fixtures."""
        self.client = MagicMock()
        self.client.create_channel = AsyncMock()
        self.client.update_channel = AsyncMock()
        self.client.create_channel_group = AsyncMock()

        self.channels = [
            {"id": 1, "name": "ESPN", "tvg_id": "ESPN.US", "channel_number": 100, "streams": [101], "auto_created": True},
        ]
        self.groups = [
            {"id": 1, "name": "Sports"},
        ]
        self.executor = ActionExecutor(
            self.client,
            existing_channels=self.channels,
            existing_groups=self.groups
        )

        self.stream_ctx = StreamContext(
            stream_id=201,
            stream_name="ESPN HD",
            m3u_account_id=1,
            m3u_account_name="Provider A",
            group_name="Sports",
            tvg_id="ESPN.US",
            resolution_height=1080,
            logo_url="http://example.com/espn.png"
        )

    def test_execute_unknown_action_type(self):
        """Execute fails for unknown action type."""
        action = {"type": "unknown_action"}
        exec_ctx = ExecutionContext()

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is False
        assert "Unknown action type" in result.error

    def test_execute_skip(self):
        """Execute skip action."""
        action = {"type": "skip"}
        exec_ctx = ExecutionContext()

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is True
        assert result.skipped is True
        assert exec_ctx.streams_skipped == 1

    def test_execute_stop_processing(self):
        """Execute stop_processing action."""
        action = {"type": "stop_processing"}
        exec_ctx = ExecutionContext()

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is True
        assert result.action_type == "stop_processing"

    def test_execute_log_match(self):
        """Execute log_match action."""
        action = {
            "type": "log_match",
            "message": "Matched stream {stream_name}"  # Params at top level
        }
        exec_ctx = ExecutionContext()

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is True
        assert "ESPN HD" in result.description


class TestActionExecutorCreateChannel:
    """Tests for create_channel action."""

    def setup_method(self):
        """Set up test fixtures."""
        self.client = MagicMock()
        self.client.create_channel = AsyncMock(return_value={"id": 99, "name": "ESPN2"})
        self.client.update_channel = AsyncMock()

        self.channels = [
            {"id": 1, "name": "ESPN", "channel_number": 100, "streams": [101], "auto_created": True},
        ]
        self.groups = [{"id": 1, "name": "Sports"}]
        self.executor = ActionExecutor(
            self.client,
            existing_channels=self.channels,
            existing_groups=self.groups
        )

        self.stream_ctx = StreamContext(
            stream_id=201,
            stream_name="ESPN2 HD",
            m3u_account_id=1,
            m3u_account_name="Provider A",
            group_name="Sports",
            tvg_id="ESPN2.US",
            resolution_height=1080,
            logo_url="http://example.com/espn2.png"
        )

    def test_create_channel_new(self):
        """Create new channel successfully."""
        action = {
            "type": "create_channel",
            "name_template": "{stream_name}"  # Params at top level
        }
        exec_ctx = ExecutionContext()

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is True
        assert result.created is True
        assert result.entity_type == "channel"
        self.client.create_channel.assert_called_once()

    def test_create_channel_dry_run(self):
        """Create channel in dry run mode doesn't call API."""
        action = {
            "type": "create_channel",
            "name_template": "{stream_name}"  # Params at top level
        }
        exec_ctx = ExecutionContext(dry_run=True)

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is True
        assert result.created is True
        assert "Would create" in result.description
        self.client.create_channel.assert_not_called()

    def test_create_channel_exists_skip(self):
        """Create channel skips if exists and if_exists=skip."""
        self.stream_ctx.stream_name = "ESPN"  # Matches existing
        action = {
            "type": "create_channel",
            "name_template": "{stream_name}",
            "if_exists": "skip"  # Params at top level
        }
        exec_ctx = ExecutionContext()

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is True
        assert result.skipped is True
        assert "already exists" in result.description
        self.client.create_channel.assert_not_called()

    def test_create_channel_exists_merge(self):
        """Create channel merges if exists and if_exists=merge."""
        self.stream_ctx.stream_name = "ESPN"  # Matches existing
        action = {
            "type": "create_channel",
            "name_template": "{stream_name}",
            "if_exists": "merge"  # Params at top level
        }
        exec_ctx = ExecutionContext()

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is True
        # Should call update_channel to add stream
        self.client.update_channel.assert_called_once()

    def test_create_channel_with_group(self):
        """Create channel with target group."""
        action = {
            "type": "create_channel",
            "name_template": "{stream_name}",
            "group_id": 1  # Params at top level
        }
        exec_ctx = ExecutionContext()

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is True
        call_args = self.client.create_channel.call_args[0][0]
        assert call_args["channel_group_id"] == 1

    def test_create_channel_template_expansion(self):
        """Create channel expands template variables."""
        self.stream_ctx.stream_name = "ESPN News"
        self.stream_ctx.resolution_height = 1080
        action = {
            "type": "create_channel",
            "name_template": "{stream_name} ({quality})"  # Params at top level
        }
        exec_ctx = ExecutionContext()

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is True
        call_args = self.client.create_channel.call_args[0][0]
        assert call_args["name"] == "ESPN News (1080p)"


class TestCreateChannelNormalizationLookup:
    """Regression tests for GH-104 Part 2: the create_channel action must not
    create a duplicate channel when an existing channel's name would collapse
    to the same normalized form as the stream being processed.
    """

    def _make_engine(self, mapping):
        """Fake normalization engine that maps names via ``mapping``."""
        engine = MagicMock()

        def _normalize(name, *args, **kwargs):
            result = MagicMock()
            result.normalized = mapping.get(name, name)
            result.transformations = []
            return result

        engine.normalize.side_effect = _normalize
        engine.extract_core_name.side_effect = lambda n: mapping.get(n, n)
        engine.extract_call_sign.return_value = None
        return engine

    def test_attaches_to_normalized_existing_instead_of_creating_duplicate(self):
        """Stream 'RTL ᴿᴬᵂ' attaches to existing 'RTL' via normalized lookup."""
        from channel_pipeline_executor import ActionExecutor

        client = MagicMock()
        client.update_channel = AsyncMock()
        client.create_channel = AsyncMock()

        # Existing channel already stored under the normalized name.
        existing = [{"id": 42, "name": "RTL", "streams": [], "channel_group_id": 5, "auto_created": True}]
        engine = self._make_engine({"RTL ᴿᴬᵂ": "RTL", "RTL": "RTL"})
        executor = ActionExecutor(client, existing_channels=existing,
                                  normalization_engine=engine)

        stream_ctx = StreamContext(
            stream_id=77,
            stream_name="RTL ᴿᴬᵂ",
            m3u_account_id=1,
            m3u_account_name="Provider",
            group_name="DE",
            tvg_id=None,
            resolution_height=None,
            logo_url=None,
        )

        action = {
            "type": "create_channel",
            "name_template": "{stream_name}",
            "if_exists": "merge",
            # normalize_names=False on this rule on purpose — the fix should
            # still prevent the duplicate via the lookup fallback.
        }

        result = asyncio.get_event_loop().run_until_complete(
            executor.execute(action, stream_ctx, ExecutionContext())
        )

        assert result.success is True
        # Must NOT create a brand-new duplicate channel.
        client.create_channel.assert_not_called()
        # Must attach the stream to the existing channel instead.
        client.update_channel.assert_called()
        updated_id = client.update_channel.call_args[0][0]
        assert updated_id == 42

    def test_number_prefix_rename_path_still_fires(self):
        """The existing rename path at executor.py:517-539 still triggers
        when ``normalization_group_ids`` is non-empty and the channel has a
        number prefix whose core differs from the normalized incoming name
        ('107 | RTL ᴿᴬᵂ' stays number-prefixed as '107 | RTL')."""
        from channel_pipeline_executor import ActionExecutor

        client = MagicMock()
        client.update_channel = AsyncMock(return_value={})
        client.create_channel = AsyncMock()

        existing = [{"id": 7, "name": "107 | RTL ᴿᴬᵂ", "channel_number": 107,
                     "streams": [], "channel_group_id": 5, "auto_created": True}]
        engine = self._make_engine({"RTL ᴿᴬᵂ": "RTL", "RTL": "RTL",
                                     "107 | RTL ᴿᴬᵂ": "107 | RTL"})
        executor = ActionExecutor(client, existing_channels=existing,
                                  normalization_engine=engine)

        stream_ctx = StreamContext(
            stream_id=77,
            stream_name="RTL ᴿᴬᵂ",
            m3u_account_id=1,
            m3u_account_name="Provider",
            group_name="DE",
            tvg_id=None,
            resolution_height=None,
            logo_url=None,
        )

        action = {
            "type": "create_channel",
            "name_template": "{stream_name}",
            "if_exists": "merge",
        }

        result = asyncio.get_event_loop().run_until_complete(
            executor.execute(action, stream_ctx, ExecutionContext(),
                             normalization_group_ids=[1])
        )

        assert result.success is True
        client.create_channel.assert_not_called()
        # update_channel is called at least for the rename; may also be called
        # to attach the stream. Verify the rename call happened.
        rename_calls = [
            c for c in client.update_channel.call_args_list
            if c[0][0] == 7 and "name" in c[0][1]
        ]
        assert len(rename_calls) >= 1
        assert rename_calls[0][0][1]["name"] == "107 | RTL"


class TestActionExecutorCreateGroup:
    """Tests for create_group action."""

    def setup_method(self):
        """Set up test fixtures."""
        self.client = MagicMock()
        self.client.create_channel_group = AsyncMock(return_value={"id": 99, "name": "Movies"})

        self.groups = [{"id": 1, "name": "Sports"}]
        self.executor = ActionExecutor(
            self.client,
            existing_groups=self.groups
        )

        self.stream_ctx = StreamContext(
            stream_id=201,
            stream_name="HBO HD",
            m3u_account_id=1,
            m3u_account_name="Provider A",
            group_name="Movies",
        )

    def test_create_group_new(self):
        """Create new group successfully."""
        action = {
            "type": "create_group",
            "name_template": "{stream_group}"  # Params at top level
        }
        exec_ctx = ExecutionContext()

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is True
        assert result.created is True
        assert result.entity_type == "group"
        assert exec_ctx.current_group_id == 99

    def test_create_group_dry_run(self):
        """Create group in dry run mode doesn't call API."""
        action = {
            "type": "create_group",
            "name_template": "{stream_group}"  # Params at top level
        }
        exec_ctx = ExecutionContext(dry_run=True)

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is True
        assert result.created is True
        assert "Would create" in result.description
        self.client.create_channel_group.assert_not_called()

    def test_create_group_exists_use_existing(self):
        """Create group uses existing if if_exists=use_existing."""
        self.stream_ctx.group_name = "Sports"  # Matches existing
        action = {
            "type": "create_group",
            "name_template": "{stream_group}",
            "if_exists": "use_existing"  # Params at top level
        }
        exec_ctx = ExecutionContext()

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is True
        assert result.skipped is True
        assert exec_ctx.current_group_id == 1  # Existing group ID
        self.client.create_channel_group.assert_not_called()

    def test_create_group_empty_name(self):
        """Create group fails with empty name."""
        self.stream_ctx.group_name = ""
        action = {
            "type": "create_group",
            "name_template": "{stream_group}"  # Params at top level
        }
        exec_ctx = ExecutionContext()

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is False
        assert "empty" in result.error.lower()


class TestActionExecutorMergeStreams:
    """Tests for merge_streams action."""

    def setup_method(self):
        """Set up test fixtures."""
        self.client = MagicMock()
        self.client.update_channel = AsyncMock()

        self.channels = [
            {"id": 1, "name": "ESPN", "tvg_id": "ESPN.US", "channel_number": 100, "streams": [101], "auto_created": True},
            {"id": 2, "name": "ESPN2", "tvg_id": "ESPN2.US", "channel_number": 101, "streams": [], "auto_created": True},
        ]
        self.executor = ActionExecutor(
            self.client,
            existing_channels=self.channels
        )

        self.stream_ctx = StreamContext(
            stream_id=201,
            stream_name="ESPN HD Backup",
            m3u_account_id=1,
            m3u_account_name="Provider A",
            tvg_id="ESPN.US",
        )

    def test_merge_by_tvg_id(self):
        """Merge stream to channel by TVG ID."""
        action = {
            "type": "merge_streams",
            "target": "existing_channel",
            "find_channel_by": "tvg_id"  # Params at top level
        }
        exec_ctx = ExecutionContext()

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is True
        assert result.modified is True
        self.client.update_channel.assert_called_once()

    def test_merge_by_name_exact(self):
        """Merge stream to channel by exact name."""
        action = {
            "type": "merge_streams",
            "target": "existing_channel",
            "find_channel_by": "name_exact",
            "find_channel_value": "ESPN"  # Params at top level
        }
        exec_ctx = ExecutionContext()

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is True
        assert result.modified is True

    def test_merge_by_name_regex(self):
        """Merge stream to channel by regex."""
        action = {
            "type": "merge_streams",
            "target": "existing_channel",
            "find_channel_by": "name_regex",
            "find_channel_value": "^ESPN$"  # Params at top level
        }
        exec_ctx = ExecutionContext()

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is True

    def test_merge_channel_not_found(self):
        """Merge fails if channel not found with existing_channel target."""
        action = {
            "type": "merge_streams",
            "target": "existing_channel",
            "find_channel_by": "name_exact",
            "find_channel_value": "FOX"  # Params at top level
        }
        exec_ctx = ExecutionContext()

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is False
        assert "not found" in result.error.lower()

    def test_merge_auto_not_found(self):
        """Merge with auto target skips if no match found."""
        self.stream_ctx.tvg_id = "UNKNOWN.US"
        action = {
            "type": "merge_streams",
            "target": "auto",
            "find_channel_by": "tvg_id"  # Params at top level
        }
        exec_ctx = ExecutionContext()

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is True
        assert result.skipped is True

    def test_merge_stream_already_in_channel(self):
        """Merge skips if stream already in channel."""
        self.stream_ctx.stream_id = 101  # Already in ESPN
        action = {
            "type": "merge_streams",
            "target": "existing_channel",
            "find_channel_by": "name_exact",
            "find_channel_value": "ESPN"  # Params at top level
        }
        exec_ctx = ExecutionContext()

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is True
        assert result.skipped is True
        self.client.update_channel.assert_not_called()

    def test_merge_remove_non_matching_prunes_channel(self):
        """When remove_non_matching is enabled, prune removes streams not merged this run."""
        # Channel has one existing stream plus an extra stale stream
        self.channels[0]["streams"] = [101, 999]
        self.executor._channel_by_id[1]["streams"] = [101, 999]

        action = {
            "type": "merge_streams",
            "target": "existing_channel",
            "find_channel_by": "tvg_id",
            "remove_non_matching": True,
        }

        exec_ctx = ExecutionContext()
        # Simulate that the existing in-channel stream also matched this run
        # (merge_streams would be executed and skipped because it's already present,
        # but it must still count as "desired" so it isn't pruned).
        existing_ctx = StreamContext(
            stream_id=101,
            stream_name="ESPN",
            m3u_account_id=1,
            m3u_account_name="Provider A",
            tvg_id="ESPN.US",
        )
        _ = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, existing_ctx, exec_ctx)
        )

        # Merge a new matching stream into ESPN
        _ = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        # Apply prune pass
        results = {"dry_run_results": [], "execution_log": []}
        asyncio.get_event_loop().run_until_complete(
            self.executor.prune_merge_streams(results, dry_run=False)
        )

        # update_channel should be called for merge and then for prune
        assert self.client.update_channel.call_count >= 2
        # Last call is the prune, and it should remove 999 while keeping merged stream 201.
        _channel_id, payload = self.client.update_channel.call_args_list[-1].args
        assert _channel_id == 1
        assert payload["streams"] == [101, 201]
        assert 999 not in payload["streams"]

    def test_merge_remove_non_matching_dry_run_logs_only(self):
        """Dry-run prune should not call update_channel and should emit dry_run_results."""
        # Channel has one existing stream plus an extra stale stream
        self.channels[0]["streams"] = [101, 999]
        self.executor._channel_by_id[1]["streams"] = [101, 999]

        action = {
            "type": "merge_streams",
            "target": "existing_channel",
            "find_channel_by": "tvg_id",
            "remove_non_matching": True,
        }

        exec_ctx = ExecutionContext(dry_run=True)
        _ = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        results = {"dry_run_results": [], "execution_log": []}
        asyncio.get_event_loop().run_until_complete(
            self.executor.prune_merge_streams(results, dry_run=True)
        )

        self.client.update_channel.assert_not_called()
        assert any("Would remove" in r.get("action", "") for r in results["dry_run_results"])


class TestMergeStreamsExactDefaultMatch:
    """Regression tests for bd-0emgo.1: merge_streams target=auto must default
    to EXACT normalized-name equality and must NOT run the legacy fuzzy cascade
    (core-name / deparen / word-prefix containment / call-sign) unless the
    ``loose_name_match`` action flag is True.

    Reproduces the production failure: a "SKY Sport 4K" channel (core name
    "sky sport" because "4K" is stripped as a quality suffix) over-matched 75
    unrelated streams ("Sky Sport Bundesliga", "SKY SPORT TENNIS", ...) because
    "sky sport" is a leading word-prefix of all of them.
    """

    def _make_engine(self, core_map):
        """Fake normalization engine.

        ``core_map`` maps a raw name -> its core name (lowercased core used by
        the executor's _core_name_to_channel index and the word-prefix step).
        normalize() returns the core as the normalized form so the exact-key
        indices (_normalized_name_to_channel) are keyed under the core too.
        """
        engine = MagicMock()

        def _normalize(name, *args, **kwargs):
            result = MagicMock()
            result.normalized = core_map.get(name, name)
            result.transformations = []
            return result

        engine.normalize.side_effect = _normalize
        engine.extract_core_name.side_effect = lambda n: core_map.get(n, n)
        engine.extract_call_sign.return_value = None
        return engine

    def _build(self):
        client = MagicMock()
        client.update_channel = AsyncMock(return_value={})
        # Existing channel "SKY Sport 4K" — its core/normalized form is
        # "sky sport" ("4K" stripped as a quality suffix).
        existing = [{"id": 50, "name": "SKY Sport 4K", "streams": [],
                     "channel_group_id": 9, "auto_created": True}]
        core_map = {
            "SKY Sport 4K": "sky sport",
            # Unrelated streams that share the "sky sport" leading prefix.
            "Sky Sport Bundesliga": "sky sport bundesliga",
            "SKY SPORT TENNIS": "sky sport tennis",
            "SKY SPORT 251": "sky sport 251",
            # A genuinely-matching stream whose core equals the channel core.
            "SKY Sport 4K UHD": "sky sport",
        }
        engine = self._make_engine(core_map)
        executor = ActionExecutor(client, existing_channels=existing,
                                  normalization_engine=engine)
        return client, executor

    def _stream(self, sid, name):
        return StreamContext(
            stream_id=sid,
            stream_name=name,
            m3u_account_id=1,
            m3u_account_name="Provider",
            group_name="Sports",
            tvg_id=None,
        )

    def _merge(self, executor, stream_ctx, loose=False):
        action = {"type": "merge_streams", "target": "auto"}
        if loose:
            action["loose_name_match"] = True
        return asyncio.get_event_loop().run_until_complete(
            executor.execute(action, stream_ctx, ExecutionContext(),
                             normalization_group_ids=[1])
        )

    def test_default_does_not_overmatch_via_word_prefix(self):
        """DEFAULT (exact-only): a 'Sky Sport Bundesliga' stream must NOT merge
        into 'SKY Sport 4K' — the word-prefix containment cascade is disabled.
        """
        client, executor = self._build()
        result = self._merge(executor, self._stream(201, "Sky Sport Bundesliga"))
        # Skipped — no exact-name channel for "sky sport bundesliga".
        assert result.skipped is True
        client.update_channel.assert_not_called()

    def test_default_does_not_overmatch_multiple_unrelated_streams(self):
        """DEFAULT: none of the unrelated 'SKY SPORT *' streams merge."""
        client, executor = self._build()
        for sid, name in [(301, "SKY SPORT TENNIS"), (302, "SKY SPORT 251")]:
            result = self._merge(executor, self._stream(sid, name))
            assert result.skipped is True, f"{name} should not merge by default"
        client.update_channel.assert_not_called()

    def test_loose_flag_restores_word_prefix_match(self):
        """loose_name_match=True restores the legacy fuzzy cascade: the
        'Sky Sport Bundesliga' stream merges into 'SKY Sport 4K' (id=50).
        """
        client, executor = self._build()
        result = self._merge(executor, self._stream(201, "Sky Sport Bundesliga"),
                             loose=True)
        assert result.success is True
        client.update_channel.assert_called()
        assert client.update_channel.call_args[0][0] == 50

    def test_exact_normalized_name_merges_by_default(self):
        """DEFAULT: a stream whose normalized/core name EXACTLY equals the
        channel's ('sky sport') DOES merge into 'SKY Sport 4K'.
        """
        client, executor = self._build()
        result = self._merge(executor, self._stream(401, "SKY Sport 4K UHD"))
        assert result.success is True
        client.update_channel.assert_called()
        assert client.update_channel.call_args[0][0] == 50


class TestNormalizeThenMergeIdentity:
    """GH #845: rule-scoped normalization is the merge handoff identity."""

    @staticmethod
    def _normalizer(raw_name: str, normalized_name: str):
        engine = MagicMock()

        def normalize(value, group_ids=None):
            result = MagicMock()
            result.normalized = (
                normalized_name if group_ids in (None, [41]) else raw_name
            )
            return result

        engine.normalize.side_effect = normalize
        engine.extract_core_name.return_value = raw_name
        engine.extract_call_sign.return_value = None
        return engine

    @staticmethod
    def _stream(name="Provider synthetic", stream_id=901):
        return StreamContext(
            stream_id=stream_id,
            stream_name=name,
            m3u_account_id=1,
            m3u_account_name="Synthetic provider",
            group_name="Synthetic group",
            tvg_id=None,
        )

    @pytest.mark.asyncio
    async def test_rule_scoped_normalized_identity_merges_existing_without_duplicates(self):
        client = MagicMock()
        client.update_channel = AsyncMock(return_value={})
        target = {
            "id": 51,
            "name": "Canonical synthetic",
            "streams": [],
            "auto_created": True,
        }
        executor = ActionExecutor(
            client,
            existing_channels=[target],
            normalization_engine=self._normalizer(
                "Provider synthetic", "Canonical synthetic"
            ),
        )

        evaluator = ConditionEvaluator(
            [dict(target, channel_group_id=7)],
            [{"id": 7, "name": "Synthetic target group"}],
            normalization_engine=executor._normalization_engine,
        )
        condition = evaluator.evaluate(
            {"type": "normalized_name_in_group", "value": 7}, self._stream()
        )
        assert condition.matched is True
        result = None
        if condition.matched:
            result = await executor.execute(
                {
                    "type": "merge_streams",
                    "target": "auto",
                    "find_channel_by": "name_exact",
                },
                self._stream(),
                ExecutionContext(),
                normalization_group_ids=[41],
            )

        assert result is not None
        assert result.success is True
        client.update_channel.assert_awaited_once_with(51, {"streams": [901]})
        assert len(executor.existing_channels) == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("targets", [[], [61, 62]])
    async def test_absent_or_ambiguous_normalized_identity_skips(self, targets):
        client = MagicMock()
        client.update_channel = AsyncMock(return_value={})
        channels = [
            {
                "id": channel_id,
                "name": "Canonical synthetic",
                "streams": [],
                "auto_created": True,
            }
            for channel_id in targets
        ]
        executor = ActionExecutor(
            client,
            existing_channels=channels,
            normalization_engine=self._normalizer(
                "Provider synthetic", "Canonical synthetic"
            ),
        )

        result = await executor.execute(
            {"type": "merge_streams", "target": "auto"},
            self._stream(),
            ExecutionContext(),
            normalization_group_ids=[41],
        )

        assert result.skipped is True
        client.update_channel.assert_not_awaited()
        assert len(executor.existing_channels) == len(targets)

    @pytest.mark.asyncio
    async def test_same_run_created_normalized_target_is_reused(self):
        client = MagicMock()
        client.update_channel = AsyncMock(return_value={})
        client.create_channel = AsyncMock(
            return_value={
                "id": 71,
                "name": "Canonical synthetic",
                "streams": [900],
            }
        )
        executor = ActionExecutor(
            client,
            existing_channels=[],
            normalization_engine=self._normalizer(
                "Provider synthetic", "Canonical synthetic"
            ),
        )
        create_result = await executor.execute(
            {
                "type": "create_channel",
                "name_template": "{normalized_name}",
                "group_id": 7,
            },
            self._stream(stream_id=900),
            ExecutionContext(),
            normalization_group_ids=[41],
        )

        merge_result = await executor.execute(
            {
                "type": "merge_streams",
                "target": "auto",
                "find_channel_by": "name_exact",
            },
            self._stream(),
            ExecutionContext(),
            normalization_group_ids=[41],
        )

        assert create_result.created is True
        assert merge_result.success is True
        client.create_channel.assert_awaited_once()
        client.update_channel.assert_awaited_once_with(71, {"streams": [900, 901]})
        assert len(executor._created_channels) == 1


class TestActionExecutorPropertyActions:
    """Tests for property assignment actions."""

    def setup_method(self):
        """Set up test fixtures."""
        self.client = MagicMock()
        self.client.update_channel = AsyncMock()
        self.client.create_logo = AsyncMock(return_value={"id": 42})
        self.client.find_logo_by_url = AsyncMock(return_value=None)

        self.channels = [
            {"id": 1, "name": "ESPN", "logo_url": None, "tvg_id": None, "auto_created": True},
        ]
        self.executor = ActionExecutor(
            self.client,
            existing_channels=self.channels
        )

        self.stream_ctx = StreamContext(
            stream_id=201,
            stream_name="ESPN HD",
            m3u_account_id=1,
            logo_url="http://example.com/espn.png",
            tvg_id="ESPN.US",
        )

    def test_assign_logo_no_channel_context(self):
        """Assign logo fails without channel context."""
        action = {"type": "assign_logo", "value": "from_stream"}  # Params at top level
        exec_ctx = ExecutionContext()

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is False
        assert "No channel" in result.error

    def test_assign_logo_from_stream(self):
        """Assign logo from stream."""
        action = {"type": "assign_logo", "value": "from_stream"}  # Params at top level
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 1

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is True
        assert result.modified is True
        self.client.update_channel.assert_called_with(1, {"logo_id": 42})

    def test_assign_logo_explicit_url(self):
        """Assign explicit logo URL."""
        # Explicit URL should override from_stream behavior
        self.stream_ctx.logo_url = None  # Clear stream logo
        action = {"type": "assign_logo", "value": "http://other.com/logo.png"}  # Params at top level
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 1

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is True
        self.client.update_channel.assert_called_with(1, {"logo_id": 42})

    def test_assign_logo_no_url_skips(self):
        """Assign logo skips if no URL available."""
        self.stream_ctx.logo_url = None
        action = {"type": "assign_logo", "value": "from_stream"}  # Params at top level
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 1

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is True
        assert result.skipped is True
        self.client.update_channel.assert_not_called()

    def test_assign_tvg_id_from_stream(self):
        """Assign tvg_id from stream."""
        action = {"type": "assign_tvg_id", "value": "from_stream"}  # Params at top level
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 1

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is True
        self.client.update_channel.assert_called_with(1, {"tvg_id": "ESPN.US"})

    def test_assign_epg(self):
        """Assign EPG source — resolves epg_id (source) to epg_data_id (data entry)."""
        # Create executor with EPG data entries
        epg_data = [{"id": 42, "tvg_id": "dummy_epg", "epg_source": 5}]
        executor = ActionExecutor(
            self.client,
            existing_channels=self.channels,
            epg_data=epg_data
        )

        action = {"type": "assign_epg", "epg_id": 5}  # Params at top level
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 1

        result = asyncio.get_event_loop().run_until_complete(
            executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is True
        self.client.update_channel.assert_called_with(1, {"epg_data_id": 42})

    def test_assign_epg_missing_id(self):
        """Assign EPG fails without epg_id."""
        action = {"type": "assign_epg"}  # No params - missing epg_id
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 1

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is False
        assert "Missing epg_id" in result.error

    def test_assign_epg_with_set_tvg_id(self):
        """Assign EPG with set_tvg_id sends both epg_data_id and tvg_id."""
        epg_data = [{"id": 42, "tvg_id": "ESPN.US", "epg_source": 5}]
        executor = ActionExecutor(
            self.client,
            existing_channels=self.channels,
            epg_data=epg_data
        )

        action = {"type": "assign_epg", "epg_id": 5, "set_tvg_id": True}
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 1

        result = asyncio.get_event_loop().run_until_complete(
            executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is True
        self.client.update_channel.assert_called_with(
            1, {"epg_data_id": 42, "tvg_id": "ESPN.US"}
        )

    def test_assign_epg_without_set_tvg_id(self):
        """Assign EPG without set_tvg_id only sends epg_data_id (existing behavior)."""
        epg_data = [{"id": 42, "tvg_id": "ESPN.US", "epg_source": 5}]
        executor = ActionExecutor(
            self.client,
            existing_channels=self.channels,
            epg_data=epg_data
        )

        action = {"type": "assign_epg", "epg_id": 5}
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 1

        result = asyncio.get_event_loop().run_until_complete(
            executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is True
        self.client.update_channel.assert_called_with(1, {"epg_data_id": 42})

    def test_assign_epg_set_tvg_id_dry_run(self):
        """Dry run with set_tvg_id updates simulated channel and description."""
        epg_data = [{"id": 42, "tvg_id": "ESPN.US", "epg_source": 5}]
        executor = ActionExecutor(
            self.client,
            existing_channels=self.channels,
            epg_data=epg_data
        )

        action = {"type": "assign_epg", "epg_id": 5, "set_tvg_id": True}
        exec_ctx = ExecutionContext(dry_run=True)
        exec_ctx.current_channel_id = 1

        result = asyncio.get_event_loop().run_until_complete(
            executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is True
        assert "set tvg_id to 'ESPN.US'" in result.description
        # Simulated channel should be updated
        assert executor._channel_by_id[1]["tvg_id"] == "ESPN.US"
        self.client.update_channel.assert_not_called()

    def test_assign_profile(self):
        """Assign stream profile."""
        action = {"type": "assign_profile", "profile_id": 3}  # Params at top level
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 1

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is True
        self.client.update_channel.assert_called_with(1, {"stream_profile_id": 3})

    def test_set_channel_number(self):
        """Set channel number."""
        action = {"type": "set_channel_number", "value": 999}  # Params at top level
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 1

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is True
        self.client.update_channel.assert_called_with(1, {"channel_number": 999})


class TestSortGroupAction:
    """Tests for the sort_group action (enhancedchannelmanager-vy4fl).

    sort_group never calls the client directly — it only queues the
    resolved group_id + params onto exec_ctx.sort_group_requests for the
    engine's post-run Pass 3.6 to consume. These tests lock the group
    resolution precedence and the queuing behavior.
    """

    def setup_method(self):
        self.client = MagicMock()
        self.channels = [
            {"id": 1, "name": "ESPN", "channel_group_id": 10, "auto_created": True},
        ]
        self.groups = [{"id": 10, "name": "Sports"}, {"id": 20, "name": "News"}]
        self.executor = ActionExecutor(
            self.client,
            existing_channels=self.channels,
            existing_groups=self.groups,
        )
        self.stream_ctx = StreamContext(
            stream_id=201,
            stream_name="ESPN HD",
            m3u_account_id=1,
        )

    def test_resolves_group_from_explicit_group_id_param(self):
        action = {"type": "sort_group", "group_id": 20}
        exec_ctx = ExecutionContext()

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is True
        assert exec_ctx.sort_group_requests == {
            20: {"order": "asc", "starting_number": None, "strip_numbers": True, "ignore_country": False}
        }

    def test_resolves_group_from_current_group_id(self):
        action = {"type": "sort_group"}
        exec_ctx = ExecutionContext()
        exec_ctx.current_group_id = 20

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is True
        assert 20 in exec_ctx.sort_group_requests

    def test_resolves_group_from_current_channel_id(self):
        action = {"type": "sort_group"}
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 1  # channel 1 is in group 10

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is True
        assert 10 in exec_ctx.sort_group_requests

    def test_resolves_group_from_rule_target_group_id_fallback(self):
        action = {"type": "sort_group"}
        exec_ctx = ExecutionContext()

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx, rule_target_group_id=30)
        )

        assert result.success is True
        assert 30 in exec_ctx.sort_group_requests

    def test_no_resolvable_group_fails(self):
        action = {"type": "sort_group"}
        exec_ctx = ExecutionContext()

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is False
        assert "No group_id" in result.error
        assert exec_ctx.sort_group_requests == {}

    def test_explicit_group_id_takes_precedence_over_current_channel(self):
        action = {"type": "sort_group", "group_id": 20}
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 1  # would resolve to group 10 otherwise

        asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert exec_ctx.sort_group_requests == {
            20: {"order": "asc", "starting_number": None, "strip_numbers": True, "ignore_country": False}
        }

    def test_queues_custom_params(self):
        action = {
            "type": "sort_group",
            "group_id": 20,
            "order": "desc",
            "starting_number": 500,
            "strip_numbers": False,
            "ignore_country": True,
        }
        exec_ctx = ExecutionContext()

        asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert exec_ctx.sort_group_requests[20] == {
            "order": "desc",
            "starting_number": 500,
            "strip_numbers": False,
            "ignore_country": True,
        }

    def test_dedupes_across_two_streams_in_same_run(self):
        """Two matched streams landing in the SAME channel/group only
        produce ONE entry in sort_group_requests (dict keyed by group_id) —
        the per-group dedup the bead requires."""
        action = {"type": "sort_group", "group_id": 20}
        exec_ctx = ExecutionContext()

        asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )
        asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert len(exec_ctx.sort_group_requests) == 1


class TestSimulatedChannelStateAfterActions:
    """Regression tests for PR #483 — action handlers must update the in-memory
    simulated channel (``_channel_by_id``) after a successful API update, so a
    later action in the same rule operates on fresh state instead of stale
    values. The headline bug: assign_tvg_id wrote the API but not the simulated
    channel, so a following assign_epg matched on the old (None) tvg_id and fell
    back to incorrect fuzzy matching.
    """

    def setup_method(self):
        """Set up test fixtures."""
        self.client = MagicMock()
        self.client.update_channel = AsyncMock()
        self.client.create_logo = AsyncMock(return_value={"id": 42})
        self.client.find_logo_by_url = AsyncMock(return_value=None)

        self.channels = [
            {
                "id": 1,
                "name": "ESPN",
                "logo_url": None,
                "tvg_id": None,
                "stream_profile_id": None,
                "channel_number": None,
                "channel_group_id": 7,
                "auto_created": True,
            },
        ]
        self.executor = ActionExecutor(
            self.client,
            existing_channels=self.channels,
        )

        self.stream_ctx = StreamContext(
            stream_id=201,
            stream_name="ESPN HD",
            m3u_account_id=1,
            logo_url="http://example.com/espn.png",
            tvg_id="ESPN.US",
        )

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def test_assign_logo_updates_simulated_state(self):
        action = {"type": "assign_logo", "value": "from_stream"}
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 1

        result = self._run(self.executor.execute(action, self.stream_ctx, exec_ctx))

        assert result.success is True
        assert self.executor._channel_by_id[1]["logo_id"] == 42

    def test_assign_tvg_id_updates_simulated_state(self):
        action = {"type": "assign_tvg_id", "value": "from_stream"}
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 1

        result = self._run(self.executor.execute(action, self.stream_ctx, exec_ctx))

        assert result.success is True
        assert self.executor._channel_by_id[1]["tvg_id"] == "ESPN.US"

    def test_assign_epg_updates_simulated_state(self):
        epg_data = [{"id": 42, "tvg_id": "ESPN.US", "epg_source": 5}]
        executor = ActionExecutor(
            self.client, existing_channels=self.channels, epg_data=epg_data
        )
        action = {"type": "assign_epg", "epg_id": 5, "set_tvg_id": True}
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 1

        result = self._run(executor.execute(action, self.stream_ctx, exec_ctx))

        assert result.success is True
        assert executor._channel_by_id[1]["epg_data_id"] == 42
        assert executor._channel_by_id[1]["tvg_id"] == "ESPN.US"

    def test_assign_profile_updates_simulated_state(self):
        action = {"type": "assign_profile", "profile_id": 3}
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 1

        result = self._run(self.executor.execute(action, self.stream_ctx, exec_ctx))

        assert result.success is True
        assert self.executor._channel_by_id[1]["stream_profile_id"] == 3

    def test_set_channel_number_updates_simulated_state(self):
        action = {"type": "set_channel_number", "value": 999}
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 1

        result = self._run(self.executor.execute(action, self.stream_ctx, exec_ctx))

        assert result.success is True
        assert self.executor._channel_by_id[1]["channel_number"] == 999

    def test_move_channel_to_uncategorized_updates_simulated_state(self):
        assert self.executor._channel_by_id[1]["channel_group_id"] == 7

        result = self._run(self.executor.move_channel_to_uncategorized(1))

        assert result.success is True
        assert self.executor._channel_by_id[1]["channel_group_id"] is None

    def test_assign_tvg_id_then_assign_epg_uses_fresh_tvg_id(self):
        """End-to-end of the PR #483 bug: assign_tvg_id then assign_epg.

        Entry A is reachable ONLY via exact tvg_id match; entry B is a fuzzy
        name-match trap. Without the simulated-state update, assign_epg would
        read the stale tvg_id (None), skip the exact-match path, and wrongly
        pick entry B. With the fix it exact-matches entry A.
        """
        epg_data = [
            {"id": 100, "tvg_id": "ESPN.US", "epg_source": 5, "name": "Sports Channel A"},
            {"id": 200, "tvg_id": "WRONG.US", "epg_source": 5, "name": "ESPN"},
        ]
        executor = ActionExecutor(
            self.client, existing_channels=self.channels, epg_data=epg_data
        )
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 1

        # Step 1: stamp the tvg_id from the stream onto the channel.
        r1 = self._run(executor.execute(
            {"type": "assign_tvg_id", "value": "from_stream"}, self.stream_ctx, exec_ctx
        ))
        assert r1.success is True
        assert executor._channel_by_id[1]["tvg_id"] == "ESPN.US"

        # Step 2: assign EPG — must exact-match entry A via the fresh tvg_id.
        r2 = self._run(executor.execute(
            {"type": "assign_epg", "epg_id": 5}, self.stream_ctx, exec_ctx
        ))
        assert r2.success is True
        self.client.update_channel.assert_called_with(1, {"epg_data_id": 100})

    def test_assign_profile_dry_run_updates_simulated_state(self):
        """Dry-run consistency follow-up: profile preview reflects in sim state."""
        action = {"type": "assign_profile", "profile_id": 3}
        exec_ctx = ExecutionContext(dry_run=True)
        exec_ctx.current_channel_id = 1

        result = self._run(self.executor.execute(action, self.stream_ctx, exec_ctx))

        assert result.success is True
        assert self.executor._channel_by_id[1]["stream_profile_id"] == 3
        self.client.update_channel.assert_not_called()

    def test_set_channel_number_dry_run_updates_simulated_state(self):
        """Dry-run consistency follow-up: number preview reflects in sim state."""
        action = {"type": "set_channel_number", "value": 999}
        exec_ctx = ExecutionContext(dry_run=True)
        exec_ctx.current_channel_id = 1

        result = self._run(self.executor.execute(action, self.stream_ctx, exec_ctx))

        assert result.success is True
        assert self.executor._channel_by_id[1]["channel_number"] == 999
        self.client.update_channel.assert_not_called()


class TestActionExecutorDryRun:
    """Tests for dry run mode across all actions."""

    def setup_method(self):
        """Set up test fixtures."""
        self.client = MagicMock()
        self.client.update_channel = AsyncMock()
        self.client.create_channel = AsyncMock()
        self.client.create_channel_group = AsyncMock()

        self.channels = [
            {"id": 1, "name": "ESPN", "channel_number": 100, "streams": [101], "auto_created": True},
        ]
        self.executor = ActionExecutor(
            self.client,
            existing_channels=self.channels
        )

        self.stream_ctx = StreamContext(
            stream_id=201,
            stream_name="ESPN2",
            m3u_account_id=1,
            tvg_id="ESPN2.US",
            logo_url="http://example.com/logo.png",
        )

    def test_dry_run_assign_logo(self):
        """Dry run doesn't call API for assign_logo."""
        action = {"type": "assign_logo", "value": "from_stream"}  # Params at top level
        exec_ctx = ExecutionContext(dry_run=True)
        exec_ctx.current_channel_id = 1

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is True
        assert "Would assign" in result.description
        self.client.update_channel.assert_not_called()

    def test_dry_run_merge_streams(self):
        """Dry run doesn't call API for merge_streams."""
        action = {
            "type": "merge_streams",
            "target": "existing_channel",
            "find_channel_by": "name_exact",
            "find_channel_value": "ESPN"  # Params at top level
        }
        exec_ctx = ExecutionContext(dry_run=True)

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is True
        assert "Would add" in result.description
        self.client.update_channel.assert_not_called()


class TestTemplateContext:
    """Tests for template context building."""

    def setup_method(self):
        """Set up test fixtures."""
        self.client = MagicMock()
        self.executor = ActionExecutor(self.client)

    def test_template_context_all_fields(self):
        """Build template context with all fields."""
        ctx = StreamContext(
            stream_id=1,
            stream_name="ESPN HD",
            m3u_account_id=1,
            m3u_account_name="Provider A",
            group_name="Sports",
            tvg_id="ESPN.US",
            tvg_name="ESPN",
            resolution_height=1080,
            normalized_name="ESPN",
        )

        template_ctx = self.executor._build_template_context(ctx)

        # Template variables use keys without braces
        assert template_ctx["stream_name"] == "ESPN HD"
        assert template_ctx["stream_group"] == "Sports"
        assert template_ctx["tvg_id"] == "ESPN.US"
        assert template_ctx["tvg_name"] == "ESPN"
        assert template_ctx["quality"] == "1080p"
        assert template_ctx["quality_raw"] == 1080
        assert template_ctx["provider"] == "Provider A"
        assert template_ctx["provider_id"] == 1
        assert template_ctx["normalized_name"] == "ESPN"

    def test_template_context_quality_4k(self):
        """Build template context with 4K quality."""
        ctx = StreamContext(
            stream_id=1,
            stream_name="ESPN 4K",
            m3u_account_id=1,
            resolution_height=2160,
        )

        template_ctx = self.executor._build_template_context(ctx)
        assert template_ctx["quality"] == "4K"

    def test_template_context_quality_720p(self):
        """Build template context with 720p quality."""
        ctx = StreamContext(
            stream_id=1,
            stream_name="ESPN",
            m3u_account_id=1,
            resolution_height=720,
        )

        template_ctx = self.executor._build_template_context(ctx)
        assert template_ctx["quality"] == "720p"

    def test_template_context_quality_480p(self):
        """Build template context with 480p quality."""
        ctx = StreamContext(
            stream_id=1,
            stream_name="ESPN SD",
            m3u_account_id=1,
            resolution_height=480,
        )

        template_ctx = self.executor._build_template_context(ctx)
        assert template_ctx["quality"] == "480p"

    def test_template_context_quality_custom(self):
        """Build template context with custom resolution (below 480p)."""
        ctx = StreamContext(
            stream_id=1,
            stream_name="ESPN",
            m3u_account_id=1,
            resolution_height=360,  # Below 480 threshold
        )

        template_ctx = self.executor._build_template_context(ctx)
        assert template_ctx["quality"] == "360p"  # Uses raw height for sub-480p

    def test_template_context_missing_optional(self):
        """Build template context with missing optional fields."""
        ctx = StreamContext(
            stream_id=1,
            stream_name="ESPN",
            m3u_account_id=1,
        )

        template_ctx = self.executor._build_template_context(ctx)

        assert template_ctx["stream_name"] == "ESPN"
        assert template_ctx["stream_group"] == ""
        assert template_ctx["tvg_id"] == ""
        assert template_ctx["quality"] == ""
        assert template_ctx["normalized_name"] == "ESPN"  # Falls back to stream_name

    def test_template_context_with_custom_variables(self):
        """Build template context includes custom variables."""
        ctx = StreamContext(
            stream_id=1,
            stream_name="ESPN",
            m3u_account_id=1,
        )
        exec_ctx = ExecutionContext()
        exec_ctx.custom_variables = {"region": "US", "suffix": "HD"}

        template_ctx = self.executor._build_template_context(ctx, exec_ctx)

        assert template_ctx["var:region"] == "US"
        assert template_ctx["var:suffix"] == "HD"
        assert template_ctx["stream_name"] == "ESPN"


class TestNormalizedNameAcrossActions:
    """GH #466 / bd-6gvt8: {normalized_name} reflects the rule's normalization
    groups in EVERY action, not just create_channel.

    Mirrors the reporter's scenario: a "Strip country prefix" group turns
    "US | ESPN" into "ESPN". Before the fix, {normalized_name} resolved to the
    raw stream name everywhere except create_channel (which alone re-normalized
    its expanded name), so Assign TVG-ID saw "US | ESPN".
    """

    def _engine_with_strip_us(self, test_session):
        from normalization_engine import NormalizationEngine
        from tests.fixtures.factories import (
            create_normalization_rule_group, create_normalization_rule,
        )
        group = create_normalization_rule_group(test_session, name="Strip US prefix", enabled=True)
        create_normalization_rule(
            test_session, group_id=group.id, name="strip 'US | '",
            condition_type="contains", condition_value="US | ",
            action_type="remove", enabled=True,
        )
        return NormalizationEngine(test_session), group.id

    def test_build_context_normalizes_with_rule_groups(self, test_session):
        """{normalized_name} = rule-normalized name when the rule has groups."""
        engine, gid = self._engine_with_strip_us(test_session)
        executor = ActionExecutor(MagicMock(), normalization_engine=engine)
        ctx = StreamContext(stream_id=1, stream_name="US | ESPN", m3u_account_id=1)

        tctx = executor._build_template_context(ctx, None, normalization_group_ids=[gid])

        assert tctx["normalized_name"] == "ESPN"

    def test_build_context_raw_when_no_groups(self, test_session):
        """Back-compat: no normalization groups -> raw stream name (unchanged)."""
        engine, gid = self._engine_with_strip_us(test_session)
        executor = ActionExecutor(MagicMock(), normalization_engine=engine)
        ctx = StreamContext(stream_id=1, stream_name="US | ESPN", m3u_account_id=1)

        tctx = executor._build_template_context(ctx, None)

        assert tctx["normalized_name"] == "US | ESPN"

    def test_assign_tvg_id_uses_rule_normalized_name(self, test_session):
        """{normalized_name} in an Assign TVG-ID value resolves to the
        rule-normalized name — the same value create_channel produces."""
        engine, gid = self._engine_with_strip_us(test_session)
        executor = ActionExecutor(MagicMock(), normalization_engine=engine)
        ctx = StreamContext(stream_id=1, stream_name="US | ESPN", m3u_account_id=1)
        exec_ctx = ExecutionContext(dry_run=True)
        exec_ctx.current_channel_id = 999
        action = {"type": "assign_tvg_id", "value": "{normalized_name}"}

        result = asyncio.get_event_loop().run_until_complete(
            executor.execute(action, ctx, exec_ctx, normalization_group_ids=[gid])
        )

        assert result.success is True
        assert "ESPN" in result.description
        assert "US |" not in result.description


class TestNameTransform:
    """Tests for name transform on create_channel and create_group."""

    def setup_method(self):
        """Set up test fixtures."""
        self.client = MagicMock()
        self.client.create_channel = AsyncMock(return_value={"id": 99, "name": "ESPN"})
        self.client.create_channel_group = AsyncMock(return_value={"id": 99, "name": "Sports"})

        self.executor = ActionExecutor(self.client)

        self.stream_ctx = StreamContext(
            stream_id=201,
            stream_name="US: ESPN HD",
            m3u_account_id=1,
            m3u_account_name="Provider A",
            group_name="US: Sports (Premium)",
        )

    def test_name_transform_strips_prefix(self):
        """Name transform strips prefix from channel name."""
        action = {
            "type": "create_channel",
            "name_template": "{stream_name}",
            "name_transform_pattern": r"^US:\s*",
            "name_transform_replacement": ""
        }
        exec_ctx = ExecutionContext()

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is True
        call_args = self.client.create_channel.call_args[0][0]
        assert call_args["name"] == "ESPN HD"

    def test_name_transform_with_backreferences(self):
        """Name transform with JS-style $1 backreferences converted to Python."""
        action = {
            "type": "create_channel",
            "name_template": "{stream_name}",
            "name_transform_pattern": r"^(\w+):\s*(.*)",
            "name_transform_replacement": "$2 ($1)"
        }
        exec_ctx = ExecutionContext()

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is True
        call_args = self.client.create_channel.call_args[0][0]
        assert call_args["name"] == "ESPN HD (US)"

    def test_name_transform_on_create_group(self):
        """Name transform works on create_group."""
        action = {
            "type": "create_group",
            "name_template": "{stream_group}",
            "name_transform_pattern": r"\s*\(.*\)$",
            "name_transform_replacement": ""
        }
        exec_ctx = ExecutionContext()

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is True
        call_args = self.client.create_channel_group.call_args[0][0]
        assert call_args == "US: Sports"

    def test_no_name_transform(self):
        """Without name transform, name is unchanged."""
        action = {
            "type": "create_channel",
            "name_template": "{stream_name}"
        }
        exec_ctx = ExecutionContext()

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is True
        call_args = self.client.create_channel.call_args[0][0]
        assert call_args["name"] == "US: ESPN HD"


class TestSetVariable:
    """Tests for set_variable action execution."""

    def setup_method(self):
        """Set up test fixtures."""
        self.client = MagicMock()
        self.client.create_channel = AsyncMock(return_value={"id": 99, "name": "ESPN"})

        self.executor = ActionExecutor(self.client)

        self.stream_ctx = StreamContext(
            stream_id=201,
            stream_name="US: ESPN HD",
            m3u_account_id=1,
            m3u_account_name="Provider A",
            group_name="Sports",
            tvg_id="ESPN.US",
        )

    def test_regex_extract_with_capture_group(self):
        """regex_extract stores first capture group."""
        action = {
            "type": "set_variable",
            "variable_name": "region",
            "variable_mode": "regex_extract",
            "source_field": "stream_name",
            "pattern": r"^(\w+):"
        }
        exec_ctx = ExecutionContext()

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is True
        assert exec_ctx.custom_variables["region"] == "US"

    def test_regex_extract_no_capture_group(self):
        """regex_extract without capture group stores full match."""
        action = {
            "type": "set_variable",
            "variable_name": "prefix",
            "variable_mode": "regex_extract",
            "source_field": "stream_name",
            "pattern": r"^\w+:"
        }
        exec_ctx = ExecutionContext()

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is True
        assert exec_ctx.custom_variables["prefix"] == "US:"

    def test_regex_extract_no_match(self):
        """regex_extract with no match stores empty string."""
        action = {
            "type": "set_variable",
            "variable_name": "missing",
            "variable_mode": "regex_extract",
            "source_field": "stream_name",
            "pattern": r"^(NOTFOUND)"
        }
        exec_ctx = ExecutionContext()

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is True
        assert exec_ctx.custom_variables["missing"] == ""

    def test_regex_replace(self):
        """regex_replace stores transformed value."""
        action = {
            "type": "set_variable",
            "variable_name": "clean_name",
            "variable_mode": "regex_replace",
            "source_field": "stream_name",
            "pattern": r"^US:\s*",
            "replacement": ""
        }
        exec_ctx = ExecutionContext()

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is True
        assert exec_ctx.custom_variables["clean_name"] == "ESPN HD"

    def test_regex_replace_with_backreference(self):
        """regex_replace converts JS-style $1 to Python \\1."""
        action = {
            "type": "set_variable",
            "variable_name": "reformatted",
            "variable_mode": "regex_replace",
            "source_field": "stream_name",
            "pattern": r"^(\w+):\s*(.*)",
            "replacement": "$2 [$1]"
        }
        exec_ctx = ExecutionContext()

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is True
        assert exec_ctx.custom_variables["reformatted"] == "ESPN HD [US]"

    def test_literal_mode(self):
        """literal mode stores expanded template."""
        action = {
            "type": "set_variable",
            "variable_name": "label",
            "variable_mode": "literal",
            "template": "{stream_name} on {provider}"
        }
        exec_ctx = ExecutionContext()

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is True
        assert exec_ctx.custom_variables["label"] == "US: ESPN HD on Provider A"

    def test_literal_with_custom_variable_reference(self):
        """literal mode can reference other custom variables."""
        exec_ctx = ExecutionContext()
        exec_ctx.custom_variables["region"] = "US"

        action = {
            "type": "set_variable",
            "variable_name": "channel_label",
            "variable_mode": "literal",
            "template": "Channel {var:region}"
        }

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is True
        assert exec_ctx.custom_variables["channel_label"] == "Channel US"

    def test_custom_variable_in_create_channel(self):
        """Custom variables accessible in create_channel template."""
        exec_ctx = ExecutionContext()
        exec_ctx.custom_variables["region"] = "US"

        action = {
            "type": "create_channel",
            "name_template": "{stream_name} [{var:region}]"
        }

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is True
        call_args = self.client.create_channel.call_args[0][0]
        assert call_args["name"] == "US: ESPN HD [US]"

    def test_set_variable_chain(self):
        """Multiple set_variable actions chain correctly."""
        exec_ctx = ExecutionContext()

        # First: extract region
        action1 = {
            "type": "set_variable",
            "variable_name": "region",
            "variable_mode": "regex_extract",
            "source_field": "stream_name",
            "pattern": r"^(\w+):"
        }
        asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action1, self.stream_ctx, exec_ctx)
        )

        # Second: build label from region
        action2 = {
            "type": "set_variable",
            "variable_name": "label",
            "variable_mode": "literal",
            "template": "Region: {var:region}"
        }
        asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action2, self.stream_ctx, exec_ctx)
        )

        assert exec_ctx.custom_variables["region"] == "US"
        assert exec_ctx.custom_variables["label"] == "Region: US"


class TestDeferredEPGAssignment:
    """Tests for deferred EPG assignment (dummy EPG sources)."""

    def setup_method(self):
        """Set up test fixtures."""
        self.client = MagicMock()
        self.client.update_channel = AsyncMock()

        self.channels = [
            {"id": 1, "name": "ESPN", "logo_url": None, "tvg_id": None, "auto_created": True},
        ]
        self.stream_ctx = StreamContext(
            stream_id=201,
            stream_name="ESPN HD",
            m3u_account_id=1,
            tvg_id="ESPN.US",
        )

    def test_assign_epg_dummy_source_no_data_defers(self):
        """assign_epg on dummy source with no data → deferred (not failed)."""
        epg_sources = [
            {"id": 9, "name": "ECM Dummy", "url": "http://localhost:6100/api/dummy-epg/xmltv/1"}
        ]
        executor = ActionExecutor(
            self.client,
            existing_channels=self.channels,
            epg_data=[],  # No data yet
            epg_sources=epg_sources,
        )

        action = {"type": "assign_epg", "epg_id": 9}
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 1

        result = asyncio.get_event_loop().run_until_complete(
            executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is True
        assert result.deferred is True
        assert "Deferred" in result.description
        assert len(executor._deferred_epg_assignments) == 1

    def test_assign_epg_non_dummy_source_no_data_fails(self):
        """assign_epg on non-dummy source with no data → still fails."""
        epg_sources = [
            {"id": 5, "name": "XMLTV Provider", "url": "http://example.com/epg.xml"}
        ]
        executor = ActionExecutor(
            self.client,
            existing_channels=self.channels,
            epg_data=[],
            epg_sources=epg_sources,
        )

        action = {"type": "assign_epg", "epg_id": 5}
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 1

        result = asyncio.get_event_loop().run_until_complete(
            executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is False
        assert result.deferred is False
        assert "No EPG data entries" in result.description

    def test_reload_epg_data_enables_retry(self):
        """After reload_epg_data(), deferred retry succeeds."""
        epg_sources = [
            {"id": 9, "name": "ECM Dummy", "url": "http://localhost:6100/api/dummy-epg/xmltv/1"}
        ]
        executor = ActionExecutor(
            self.client,
            existing_channels=self.channels,
            epg_data=[],
            epg_sources=epg_sources,
        )

        action = {"type": "assign_epg", "epg_id": 9}
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 1

        # First attempt: deferred
        result1 = asyncio.get_event_loop().run_until_complete(
            executor.execute(action, self.stream_ctx, exec_ctx)
        )
        assert result1.deferred is True

        # Simulate EPG refresh — reload with data
        executor.reload_epg_data([
            {"id": 42, "tvg_id": "espn", "epg_source": 9}
        ])

        # Retry: should succeed now
        from channel_pipeline_schema import Action
        action_obj = Action.from_dict(action)
        result2 = asyncio.get_event_loop().run_until_complete(
            executor._execute_assign_epg(action_obj, self.stream_ctx, exec_ctx)
        )

        assert result2.success is True
        assert result2.deferred is False
        self.client.update_channel.assert_called_with(1, {"epg_data_id": 42})


class TestVerifyEpgAssignments:
    """Tests for verify_epg_assignments post-execution verification."""

    def setup_method(self):
        self.client = MagicMock()
        self.client.get_channel = AsyncMock()
        self.client.update_channel = AsyncMock()

    def _make_executor(self):
        return ActionExecutor(self.client, existing_channels=[], existing_groups=[])

    def test_noop_when_no_pending(self):
        """Returns immediately with zero counts when nothing to verify."""
        executor = self._make_executor()
        ok, patched, failed = asyncio.get_event_loop().run_until_complete(
            executor.verify_epg_assignments()
        )
        assert (ok, patched, failed) == (0, 0, 0)
        self.client.get_channel.assert_not_called()

    def test_skips_when_already_persisted(self):
        """No re-PATCH when GET returns matching epg_data_id."""
        executor = self._make_executor()
        executor._pending_epg_verifications = [
            (100, {"epg_data_id": 42}),
            (200, {"epg_data_id": 99}),
        ]
        self.client.get_channel.side_effect = [
            {"id": 100, "epg_data_id": 42},
            {"id": 200, "epg_data_id": 99},
        ]

        ok, patched, failed = asyncio.get_event_loop().run_until_complete(
            executor.verify_epg_assignments()
        )
        assert (ok, patched, failed) == (2, 0, 0)
        self.client.update_channel.assert_not_called()
        # Pending list should be cleared
        assert executor._pending_epg_verifications == []

    def test_retries_on_mismatch(self):
        """Re-PATCHes when GET returns wrong epg_data_id."""
        executor = self._make_executor()
        executor._pending_epg_verifications = [
            (100, {"epg_data_id": 42, "tvg_id": "espn"}),
        ]
        # GET returns wrong value
        self.client.get_channel.return_value = {"id": 100, "epg_data_id": None}

        ok, patched, failed = asyncio.get_event_loop().run_until_complete(
            executor.verify_epg_assignments()
        )
        assert (ok, patched, failed) == (0, 1, 0)
        self.client.update_channel.assert_called_once_with(
            100, {"epg_data_id": 42, "tvg_id": "espn"}
        )

    def test_handles_get_failure(self):
        """Counts as failed when GET raises an exception."""
        executor = self._make_executor()
        executor._pending_epg_verifications = [
            (100, {"epg_data_id": 42}),
        ]
        self.client.get_channel.side_effect = Exception("Connection refused")

        ok, patched, failed = asyncio.get_event_loop().run_until_complete(
            executor.verify_epg_assignments()
        )
        assert (ok, patched, failed) == (0, 0, 1)


class TestMatchScopeTargetGroup:
    """Regression tests for GH-92 / bd-r9mtd: ``match_scope_target_group``.

    When two rules with different target groups produce the same channel name
    (e.g., both produce "ESPN"), the default (group-agnostic) lookup merges the
    second stream into the first rule's channel. With
    ``match_scope_target_group=True``, the lookup is scoped to the rule's
    target group, so each rule creates a separate channel in its own group.
    """

    def setup_method(self):
        """Set up executor with two groups and an existing ESPN channel in group 1."""
        self.client = MagicMock()
        # Return a distinguishable new channel each time create_channel is called.
        self._next_id = 500
        async def _create_channel(data):
            self._next_id += 1
            return {
                "id": self._next_id,
                "name": data["name"],
                "channel_number": data.get("channel_number"),
                "channel_group_id": data.get("channel_group_id"),
                "streams": data.get("streams", []),
            }
        self.client.create_channel = AsyncMock(side_effect=_create_channel)
        self.client.update_channel = AsyncMock()

        # Group 1 = SPORTS with an existing "ESPN" channel (simulates rule A
        # having already created one). Group 2 = ESPN-GROUP, empty.
        self.channels = [
            {"id": 1, "name": "ESPN", "channel_number": 100,
             "channel_group_id": 1, "streams": [101], "auto_created": True},
        ]
        self.groups = [
            {"id": 1, "name": "SPORTS"},
            {"id": 2, "name": "ESPN-GROUP"},
        ]
        self.executor = ActionExecutor(
            self.client,
            existing_channels=self.channels,
            existing_groups=self.groups,
        )

        self.stream_ctx = StreamContext(
            stream_id=202,
            stream_name="ESPN",
            m3u_account_id=1,
            m3u_account_name="Provider A",
            group_name="ESPN-GROUP",
            tvg_id="ESPN.US",
        )

    def test_find_channel_by_name_honors_scope_group_id(self):
        """Scope filter excludes channels in other groups."""
        in_scope = self.executor._find_channel_by_name("ESPN", scope_group_id=1)
        assert in_scope is not None and in_scope["id"] == 1

        out_of_scope = self.executor._find_channel_by_name("ESPN", scope_group_id=2)
        assert out_of_scope is None

        # None (default) preserves backwards-compat global lookup
        any_group = self.executor._find_channel_by_name("ESPN")
        assert any_group is not None and any_group["id"] == 1

    def test_default_merges_across_groups(self):
        """Without the flag, a rule targeting group 2 merges into group 1's ESPN (GH-92 bug).

        This documents the pre-fix behavior — proves the flag is needed.
        """
        action = {
            "type": "create_channel",
            "name_template": "{stream_name}",
            "if_exists": "merge",
        }
        exec_ctx = ExecutionContext()

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(
                action, self.stream_ctx, exec_ctx,
                rule_target_group_id=2,
                match_scope_target_group=False,
            )
        )
        # With flag OFF, the second rule merges into the group 1 ESPN —
        # update_channel is called, create_channel is NOT.
        assert result.success is True
        self.client.create_channel.assert_not_called()
        self.client.update_channel.assert_called()

    def test_scope_creates_separate_channel_in_target_group(self):
        """With the flag ON, rule B creates a new ESPN in group 2 instead of merging.

        This is the GH-92 regression scenario: two rules, same channel name,
        different target groups — each should produce its own channel.
        """
        action = {
            "type": "create_channel",
            "name_template": "{stream_name}",
            "if_exists": "merge",
        }
        exec_ctx = ExecutionContext()

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(
                action, self.stream_ctx, exec_ctx,
                rule_target_group_id=2,
                match_scope_target_group=True,
            )
        )
        assert result.success is True
        assert result.created is True
        # A brand-new channel was created in group 2 — NOT a merge into group 1.
        self.client.create_channel.assert_called_once()
        call_args = self.client.create_channel.call_args[0][0]
        assert call_args["name"] == "ESPN"
        assert call_args["channel_group_id"] == 2
        # And the existing group-1 channel was left alone
        self.client.update_channel.assert_not_called()

    def test_scope_matches_when_same_target_group(self):
        """Flag ON, same target group as existing channel → still merges.

        The flag only prevents cross-group merges; within the same group, an
        existing channel with the same name is still the "already exists" case
        and the rule's ``if_exists`` setting applies.
        """
        action = {
            "type": "create_channel",
            "name_template": "{stream_name}",
            "if_exists": "skip",
        }
        exec_ctx = ExecutionContext()

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(
                action, self.stream_ctx, exec_ctx,
                rule_target_group_id=1,  # same group as existing ESPN
                match_scope_target_group=True,
            )
        )
        assert result.success is True
        assert result.skipped is True
        assert result.entity_id == 1
        self.client.create_channel.assert_not_called()


class TestMatchScopeGroupId:
    """GH #298 / bd-kncun: explicit rule-level ``match_scope_group_id``.

    Migration 0002 scoped create_channel name lookups to the *action's*
    target group, but a Merge-Streams-only rule had no group to scope to and
    silently fell back to ALL groups (the reporter's symptom). The explicit
    ``rule_scope_group_id`` threads a group through BOTH create_channel and
    merge_streams name lookups, independent of any action's target group.
    NULL preserves prior behavior exactly.
    """

    def setup_method(self):
        """Two groups; an existing 'ESPN' channel in group 1 (SPORTS)."""
        self.client = MagicMock()
        self._next_id = 600

        async def _create_channel(data):
            self._next_id += 1
            return {
                "id": self._next_id,
                "name": data["name"],
                "channel_number": data.get("channel_number"),
                "channel_group_id": data.get("channel_group_id"),
                "streams": data.get("streams", []),
            }

        self.client.create_channel = AsyncMock(side_effect=_create_channel)
        self.client.update_channel = AsyncMock()

        # Existing ESPN lives in group 1 (SPORTS). Group 2 (ESPN-GROUP) is empty.
        self.channels = [
            {"id": 1, "name": "ESPN", "tvg_id": "ESPN.US", "channel_number": 100,
             "channel_group_id": 1, "streams": [101], "auto_created": True},
        ]
        self.groups = [
            {"id": 1, "name": "SPORTS"},
            {"id": 2, "name": "ESPN-GROUP"},
        ]
        self.executor = ActionExecutor(
            self.client,
            existing_channels=self.channels,
            existing_groups=self.groups,
        )
        self.stream_ctx = StreamContext(
            stream_id=202,
            stream_name="ESPN",
            m3u_account_id=1,
            m3u_account_name="Provider A",
            group_name="ESPN-GROUP",
            tvg_id="ESPN.US",
        )

    def _run(self, action, exec_ctx=None, **kwargs):
        exec_ctx = exec_ctx or ExecutionContext()
        return asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx, **kwargs)
        )

    # --- create_channel ---------------------------------------------------

    def test_create_channel_explicit_scope_group_overrides_action_group(self):
        """Explicit rule scope group wins over the action's derived group_id.

        Existing ESPN is in group 1; the action targets group 1, but the rule
        pins the scope to group 2. The group-1 ESPN must NOT be found, so a new
        ESPN is created in the action's group rather than merging.
        """
        action = {
            "type": "create_channel",
            "name_template": "{stream_name}",
            "group_id": 1,
            "if_exists": "merge",
        }
        result = self._run(
            action,
            match_scope_target_group=True,
            rule_scope_group_id=2,
        )
        assert result.success is True
        # Scope pinned to group 2 → group-1 ESPN invisible → new channel created.
        self.client.create_channel.assert_called_once()
        self.client.update_channel.assert_not_called()

    def test_create_channel_falls_back_to_action_group_when_scope_null(self):
        """NULL rule scope group → scope derives from the action group (prior behavior).

        Action targets group 1 where ESPN already exists → merge into it, no
        new channel. This is the exact pre-GH-298 create_channel path.
        """
        action = {
            "type": "create_channel",
            "name_template": "{stream_name}",
            "group_id": 1,
            "if_exists": "merge",
        }
        result = self._run(
            action,
            match_scope_target_group=True,
            rule_scope_group_id=None,
        )
        assert result.success is True
        self.client.create_channel.assert_not_called()
        self.client.update_channel.assert_called()

    # --- merge_streams ----------------------------------------------------

    def test_merge_streams_rejects_candidate_in_wrong_group(self):
        """Scope on + explicit group → a candidate in another group is rejected.

        This is the reporter's core scenario: a Merge-Streams rule scoped to
        group 2 must NOT merge into the group-1 ESPN. With no channel in scope,
        merge_streams skips (it only adds to existing channels).
        """
        action = {
            "type": "merge_streams",
            "target": "auto",
            "find_channel_by": "name_exact",
            "find_channel_value": "ESPN",
        }
        result = self._run(
            action,
            match_scope_target_group=True,
            rule_scope_group_id=2,
        )
        assert result.success is True
        assert result.skipped is True
        # No cross-group merge happened.
        self.client.update_channel.assert_not_called()

    def test_merge_streams_matches_candidate_in_scope_group(self):
        """Scope on + explicit group matching the candidate → merge proceeds."""
        action = {
            "type": "merge_streams",
            "target": "auto",
            "find_channel_by": "name_exact",
            "find_channel_value": "ESPN",
        }
        result = self._run(
            action,
            match_scope_target_group=True,
            rule_scope_group_id=1,  # ESPN's actual group
        )
        assert result.success is True
        assert result.skipped is not True
        self.client.update_channel.assert_called()

    def test_merge_streams_unchanged_when_scope_group_null(self):
        """Scope on but NULL group → group-agnostic match (prior behavior).

        merge_streams has no action group_id, so a NULL rule scope group means
        the lookup still finds the group-1 ESPN and merges — exactly as before
        GH-298 (the rule builder warns the operator about this case).
        """
        action = {
            "type": "merge_streams",
            "target": "auto",
            "find_channel_by": "name_exact",
            "find_channel_value": "ESPN",
        }
        result = self._run(
            action,
            match_scope_target_group=True,
            rule_scope_group_id=None,
        )
        assert result.success is True
        self.client.update_channel.assert_called()

    def test_merge_streams_scope_off_searches_all_groups(self):
        """Scope OFF → explicit group ignored, lookup spans all groups.

        Even with a rule_scope_group_id set, match_scope_target_group=False
        disables scoping entirely, so the group-1 ESPN is matched and merged.
        """
        action = {
            "type": "merge_streams",
            "target": "auto",
            "find_channel_by": "name_exact",
            "find_channel_value": "ESPN",
        }
        result = self._run(
            action,
            match_scope_target_group=False,
            rule_scope_group_id=2,  # ignored because scope is off
        )
        assert result.success is True
        self.client.update_channel.assert_called()


class TestMergeStreamsTargetChannelGroupFilter:
    """bd-0emgo.3: target-channel group filter on the merge_streams action.

    The ``*_in_group`` / ``normalized_name_not_in_group`` *conditions* are
    stream-side — they only gate whether a rule FIRES, never which existing
    channel merge_streams target=auto resolves to. This new action-level
    filter is a post-resolution reject (mirroring the GH #298 scope-reject at
    channel_pipeline_executor.py): after the target channel is resolved, the merge
    is skipped if the resolved channel's ``channel_group_id`` is in
    ``target_channel_not_in_group`` (or, for ``target_channel_in_group``, NOT
    in the allow-list). Resolution itself is unchanged.

    Two existing channels named "ESPN": one in group 1 (allowed), one in
    group 567 (the excluded group from the production report). The filter is
    applied AFTER resolution, so we vary which channel resolution returns by
    pointing find_channel_value at the right name.
    """

    def setup_method(self):
        self.client = MagicMock()
        self.client.create_channel = AsyncMock()
        self.client.update_channel = AsyncMock()

        # "Keep" lives in group 1; "Excluded" lives in group 567.
        self.channels = [
            {"id": 10, "name": "Keep", "channel_number": 100,
             "channel_group_id": 1, "streams": [101], "auto_created": True},
            {"id": 20, "name": "Excluded", "channel_number": 200,
             "channel_group_id": 567, "streams": [201], "auto_created": True},
        ]
        self.groups = [
            {"id": 1, "name": "ALLOWED"},
            {"id": 567, "name": "EXCLUDED"},
        ]
        self.executor = ActionExecutor(
            self.client,
            existing_channels=self.channels,
            existing_groups=self.groups,
        )

    def _stream(self, name):
        return StreamContext(
            stream_id=999,
            stream_name=name,
            m3u_account_id=1,
            m3u_account_name="Provider A",
            group_name="whatever",
        )

    def _run(self, action, stream_name, **kwargs):
        exec_ctx = ExecutionContext()
        return asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self._stream(stream_name), exec_ctx, **kwargs)
        )

    # --- target_channel_not_in_group (primary: "merge anywhere EXCEPT X") ---

    def test_not_in_group_skips_merge_into_excluded_group(self):
        """Bug-1 reproduction: the resolved target is in the excluded group →
        the merge is SKIPPED and the channel in that group receives nothing.
        """
        action = {
            "type": "merge_streams",
            "target": "auto",
            "find_channel_by": "name_exact",
            "find_channel_value": "Excluded",  # resolves to channel 20 (group 567)
            "target_channel_not_in_group": [567],
        }
        result = self._run(action, "Excluded")
        assert result.success is True
        assert result.skipped is True
        # The channel in the excluded group did NOT receive the merge.
        self.client.update_channel.assert_not_called()

    def test_not_in_group_merges_when_target_outside_excluded_group(self):
        """A stream whose resolved target is NOT in the excluded group merges."""
        action = {
            "type": "merge_streams",
            "target": "auto",
            "find_channel_by": "name_exact",
            "find_channel_value": "Keep",  # resolves to channel 10 (group 1)
            "target_channel_not_in_group": [567],
        }
        result = self._run(action, "Keep")
        assert result.success is True
        assert result.skipped is not True
        self.client.update_channel.assert_called()

    # --- target_channel_in_group (complement: "only merge into X") ---

    def test_in_group_merges_when_target_in_allowed_group(self):
        action = {
            "type": "merge_streams",
            "target": "auto",
            "find_channel_by": "name_exact",
            "find_channel_value": "Keep",  # group 1 — in allow-list
            "target_channel_in_group": [1],
        }
        result = self._run(action, "Keep")
        assert result.success is True
        assert result.skipped is not True
        self.client.update_channel.assert_called()

    def test_in_group_skips_when_target_outside_allowed_group(self):
        action = {
            "type": "merge_streams",
            "target": "auto",
            "find_channel_by": "name_exact",
            "find_channel_value": "Excluded",  # group 567 — NOT in allow-list
            "target_channel_in_group": [1],
        }
        result = self._run(action, "Excluded")
        assert result.success is True
        assert result.skipped is True
        self.client.update_channel.assert_not_called()

    # --- back-compat: no filter → unchanged ---

    def test_no_filter_merges_into_any_group(self):
        """No filter set → behavior unchanged; merges into group 567 freely."""
        action = {
            "type": "merge_streams",
            "target": "auto",
            "find_channel_by": "name_exact",
            "find_channel_value": "Excluded",
        }
        result = self._run(action, "Excluded")
        assert result.success is True
        assert result.skipped is not True
        self.client.update_channel.assert_called()

    def test_empty_not_in_group_list_is_noop(self):
        """An empty exclusion list excludes nothing — merge proceeds."""
        action = {
            "type": "merge_streams",
            "target": "auto",
            "find_channel_by": "name_exact",
            "find_channel_value": "Excluded",
            "target_channel_not_in_group": [],
        }
        result = self._run(action, "Excluded")
        assert result.success is True
        assert result.skipped is not True
        self.client.update_channel.assert_called()

    def test_not_in_group_composes_with_match_scope(self):
        """The new filter is independent of GH #298 match_scope: with scope on
        and pinned to group 567, the candidate is in scope, but the new
        exclusion still rejects it.
        """
        action = {
            "type": "merge_streams",
            "target": "auto",
            "find_channel_by": "name_exact",
            "find_channel_value": "Excluded",
            "target_channel_not_in_group": [567],
        }
        result = self._run(
            action, "Excluded",
            match_scope_target_group=True,
            rule_scope_group_id=567,
        )
        assert result.success is True
        assert result.skipped is True
        self.client.update_channel.assert_not_called()


class TestCreateChannelSuperscriptStripping:
    """
    bd-eio04.1: auto-creation _execute_create_channel with a real
    normalization engine must strip BOTH letter-superscripts
    (ᴿᴬᵂ -> RAW, ᴴᴰ -> HD) AND numeric-superscripts (ESPN² -> ESPN2)
    from the created channel name. The preserve_superscripts carve-out
    from PR #61 / bd-yui1k has been dropped — the Test Rules preview
    path and the auto-creation path now share a single
    NormalizationPolicy (GH #104).

    End-to-end coverage: uses the real NormalizationEngine against an
    in-memory test session so the full normalize() ->
    NormalizationPolicy.apply_to_text() -> create_channel path is
    exercised, not mocked.
    """

    def test_raw_letter_superscripts_stripped_in_created_channel_name(self, test_session):
        """Stream 'Foo ᴿᴬᵂ' creates channel 'Foo RAW' (ᴿᴬᵂ converted)."""
        from channel_pipeline_executor import ActionExecutor, ExecutionContext
        from normalization_engine import NormalizationEngine
        from tests.fixtures.factories import create_normalization_rule_group

        # Real engine, real session; the rule group just needs to exist so
        # the group_ids filter finds it — the superscript conversion itself
        # happens before DB rules apply.
        group = create_normalization_rule_group(
            test_session, name="noop", enabled=True,
        )
        engine = NormalizationEngine(test_session)

        client = MagicMock()
        client.create_channel = AsyncMock(return_value={"id": 77, "name": "Foo"})

        executor = ActionExecutor(
            client,
            existing_channels=[],
            existing_groups=[],
            normalization_engine=engine,
        )

        stream_ctx = StreamContext(
            stream_id=101,
            stream_name="Foo ᴿᴬᵂ",  # Foo ᴿᴬᵂ
            m3u_account_id=1,
            m3u_account_name="Provider",
            group_name="Test",
            tvg_id=None,
            resolution_height=None,
            logo_url=None,
        )

        action = {
            "type": "create_channel",
            "name_template": "{stream_name}",
        }

        result = asyncio.get_event_loop().run_until_complete(
            executor.execute(
                action, stream_ctx, ExecutionContext(),
                normalization_group_ids=[group.id],
            )
        )

        assert result.success is True
        assert result.created is True
        # The POSTed channel name must NOT contain the letter-superscripts
        client.create_channel.assert_called_once()
        posted = client.create_channel.call_args[0][0]
        assert "ᴿ" not in posted["name"]  # ᴿ
        assert "ᴬ" not in posted["name"]  # ᴬ
        assert "ᵂ" not in posted["name"]  # ᵂ
        assert posted["name"] == "Foo RAW"

    def test_numeric_superscripts_converted_in_created_channel_name(self, test_session):
        """Stream 'ESPN²' creates channel 'ESPN2' (² converted, bd-eio04.1).

        Post bd-eio04.1 / GH #104: the preserve_superscripts carve-out
        was dropped. Numeric superscripts (² ³ ⁰-⁹ ⁺⁻⁼⁽⁾) convert to
        ASCII on ALL normalization code paths — including the
        auto-creation executor path that previously preserved them.

        Supersedes the old (now-deleted) test
        `test_numeric_superscripts_preserved_in_created_channel_name`
        which locked in the broken behavior from PR #61.
        """
        from channel_pipeline_executor import ActionExecutor, ExecutionContext
        from normalization_engine import NormalizationEngine
        from tests.fixtures.factories import create_normalization_rule_group

        group = create_normalization_rule_group(
            test_session, name="noop2", enabled=True,
        )
        engine = NormalizationEngine(test_session)

        client = MagicMock()
        client.create_channel = AsyncMock(return_value={"id": 78, "name": "ESPN2"})

        executor = ActionExecutor(
            client,
            existing_channels=[],
            existing_groups=[],
            normalization_engine=engine,
        )

        stream_ctx = StreamContext(
            stream_id=102,
            stream_name="ESPN²",  # ESPN²
            m3u_account_id=1,
            m3u_account_name="Provider",
            group_name="Sports",
            tvg_id=None,
            resolution_height=None,
            logo_url=None,
        )

        action = {
            "type": "create_channel",
            "name_template": "{stream_name}",
        }

        result = asyncio.get_event_loop().run_until_complete(
            executor.execute(
                action, stream_ctx, ExecutionContext(),
                normalization_group_ids=[group.id],
            )
        )

        assert result.success is True
        client.create_channel.assert_called_once()
        posted = client.create_channel.call_args[0][0]
        assert "²" not in posted["name"]  # ² stripped/converted
        assert posted["name"] == "ESPN2"


class TestMergeJournalEntry:
    """bd-0emgo.5: per-merge journal entries for lightweight recoverability.

    Each LIVE merge in ``_add_stream_to_channel`` must queue one
    ``auto_creation``/``merge_stream`` journal entry tagged with
    ``batch_id=str(execution_id)`` so an operator can later list every
    ``(channel_id, stream_id)`` pair a run touched via
    ``get_journal(batch_id=...)``. STREAM IDs ONLY in before/after — never
    stream URLs/objects (they embed provider credentials). Dry-run writes
    nothing.
    """

    def setup_method(self):
        self.client = MagicMock()
        self.client.update_channel = AsyncMock()
        self.channels = [
            {"id": 1, "name": "ESPN", "tvg_id": "ESPN.US",
             "channel_number": 100, "streams": [101], "auto_created": True},
        ]
        self.stream_ctx = StreamContext(
            stream_id=201,
            stream_name="ESPN HD Backup",
            stream_url="http://provider.example/secret-token/201.ts",
            m3u_account_id=1,
            m3u_account_name="Provider A",
            tvg_id="ESPN.US",
        )

    def _run_live_merge(self, execution_id):
        executor = ActionExecutor(
            self.client,
            existing_channels=self.channels,
            execution_id=execution_id,
        )
        action = {
            "type": "merge_streams",
            "target": "existing_channel",
            "find_channel_by": "name_exact",
            "find_channel_value": "ESPN",
        }
        exec_ctx = ExecutionContext()  # dry_run defaults to False (live)
        result = asyncio.get_event_loop().run_until_complete(
            executor.execute(action, self.stream_ctx, exec_ctx)
        )
        executor._flush_journal_buffer()
        return result

    def test_live_merge_flushes_one_journal_entry(self):
        """A live merge flushes exactly one auto_creation/merge_stream entry."""
        with patch("channel_pipeline_executor.journal.log_entries") as mock_log:
            result = self._run_live_merge(execution_id=42)

        assert result.success is True
        assert result.modified is True
        mock_log.assert_called_once()
        entry = mock_log.call_args.kwargs["entries"][0]
        assert entry["category"] == "auto_creation"
        assert entry["action_type"] == "merge_stream"
        assert entry["entity_id"] == 1  # channel_id
        assert entry["entity_name"] == "ESPN"
        assert entry["user_initiated"] is False

    def test_journal_batch_id_is_execution_id(self):
        """batch_id carries the run's execution_id (as a string)."""
        with patch("channel_pipeline_executor.journal.log_entries") as mock_log:
            self._run_live_merge(execution_id=42)

        entry = mock_log.call_args.kwargs["entries"][0]
        assert entry["batch_id"] == "42"

    def test_journal_before_after_carry_stream_ids_only(self):
        """before/after hold stream IDs only — never URLs/objects (creds)."""
        with patch("channel_pipeline_executor.journal.log_entries") as mock_log:
            self._run_live_merge(execution_id=42)

        entry = mock_log.call_args.kwargs["entries"][0]
        before = entry["before_value"]
        after = entry["after_value"]
        assert before == {"stream_ids": [101]}
        assert after == {"stream_ids": [101, 201]}
        # Credential safety: the provider token must not leak anywhere.
        serialized = repr(entry)
        assert "secret-token" not in serialized
        assert "http" not in serialized

    def test_journal_round_trip_recovers_channel_stream_pair(self):
        """The (channel_id, stream_id) pair is recoverable from the entry."""
        with patch("channel_pipeline_executor.journal.log_entries") as mock_log:
            self._run_live_merge(execution_id=42)

        entry = mock_log.call_args.kwargs["entries"][0]
        channel_id = entry["entity_id"]
        before_ids = set(entry["before_value"]["stream_ids"])
        after_ids = set(entry["after_value"]["stream_ids"])
        merged_stream_ids = after_ids - before_ids
        assert channel_id == 1
        assert merged_stream_ids == {201}

    def test_dry_run_writes_no_journal_entry(self):
        """A dry-run merge writes NOTHING to the journal."""
        executor = ActionExecutor(
            self.client,
            existing_channels=self.channels,
            execution_id=42,
        )
        action = {
            "type": "merge_streams",
            "target": "existing_channel",
            "find_channel_by": "name_exact",
            "find_channel_value": "ESPN",
        }
        exec_ctx = ExecutionContext(dry_run=True)
        with patch("channel_pipeline_executor.journal.log_entries") as mock_log:
            result = asyncio.get_event_loop().run_until_complete(
                executor.execute(action, self.stream_ctx, exec_ctx)
            )
            executor._flush_journal_buffer()

        assert result.success is True
        assert "Would add" in result.description
        self.client.update_channel.assert_not_called()
        mock_log.assert_not_called()

    def test_multi_merge_run_flushes_one_entry_per_merge(self):
        """A run merging N streams flushes N entries, all sharing batch_id."""
        executor = ActionExecutor(
            self.client,
            existing_channels=self.channels,
            execution_id=7,
        )
        action = {
            "type": "merge_streams",
            "target": "existing_channel",
            "find_channel_by": "name_exact",
            "find_channel_value": "ESPN",
        }
        exec_ctx = ExecutionContext()
        stream_ctxs = [
            StreamContext(stream_id=sid, stream_name=f"S{sid}",
                          m3u_account_id=1, tvg_id="ESPN.US")
            for sid in (301, 302, 303)
        ]
        with patch("channel_pipeline_executor.journal.log_entries") as mock_log:
            for sc in stream_ctxs:
                asyncio.get_event_loop().run_until_complete(
                    executor.execute(action, sc, exec_ctx)
                )
            executor._flush_journal_buffer()

        mock_log.assert_called_once()
        entries = mock_log.call_args.kwargs["entries"]
        assert len(entries) == 3
        batch_ids = {entry["batch_id"] for entry in entries}
        assert batch_ids == {"7"}

    def test_merge_journal_flushes_at_threshold(self):
        """The default 100-entry buffer threshold flushes and clears itself."""
        executor = ActionExecutor(
            self.client,
            existing_channels=self.channels,
            execution_id=99,
        )
        action = {
            "type": "merge_streams",
            "target": "existing_channel",
            "find_channel_by": "name_exact",
            "find_channel_value": "ESPN",
        }
        exec_ctx = ExecutionContext()
        stream_ctxs = [
            StreamContext(stream_id=sid, stream_name=f"S{sid}",
                          m3u_account_id=1, tvg_id="ESPN.US")
            for sid in range(300, 400)
        ]

        with patch("channel_pipeline_executor.journal.log_entries") as mock_log:
            for sc in stream_ctxs:
                result = asyncio.get_event_loop().run_until_complete(
                    executor.execute(action, sc, exec_ctx)
                )
                assert result.success is True

        mock_log.assert_called_once()
        entries = mock_log.call_args.kwargs["entries"]
        assert len(entries) == executor._journal_flush_threshold == 100
        assert {entry["batch_id"] for entry in entries} == {"99"}
        assert executor._journal_buffer == []

    def test_no_execution_id_skips_journal(self):
        """Without an execution_id (direct-construct callers) no entry is written.

        execution_id is the audit-correlation primitive; entries without it
        would be unrecoverable noise, so the executor skips the journal write
        when no execution_id was threaded in (default None).
        """
        executor = ActionExecutor(
            self.client,
            existing_channels=self.channels,
        )  # no execution_id
        action = {
            "type": "merge_streams",
            "target": "existing_channel",
            "find_channel_by": "name_exact",
            "find_channel_value": "ESPN",
        }
        exec_ctx = ExecutionContext()
        with patch("channel_pipeline_executor.journal.log_entries") as mock_log:
            result = asyncio.get_event_loop().run_until_complete(
                executor.execute(action, self.stream_ctx, exec_ctx)
            )
            executor._flush_journal_buffer()

        assert result.success is True
        mock_log.assert_not_called()

    def test_already_present_stream_writes_no_journal_entry(self):
        """Re-merging an already-present stream is a skip — no journal entry."""
        executor = ActionExecutor(
            self.client,
            existing_channels=self.channels,
            execution_id=42,
        )
        stream_ctx = StreamContext(
            stream_id=101,  # already in channel 1
            stream_name="ESPN",
            m3u_account_id=1,
            tvg_id="ESPN.US",
        )
        action = {
            "type": "merge_streams",
            "target": "existing_channel",
            "find_channel_by": "name_exact",
            "find_channel_value": "ESPN",
        }
        exec_ctx = ExecutionContext()
        with patch("channel_pipeline_executor.journal.log_entries") as mock_log:
            result = asyncio.get_event_loop().run_until_complete(
                executor.execute(action, stream_ctx, exec_ctx)
            )
            executor._flush_journal_buffer()

        assert result.success is True
        assert result.skipped is True
        self.client.update_channel.assert_not_called()
        mock_log.assert_not_called()


class TestMergeCountLabels:
    """bd-0emgo.4: merge counter correctness and distinct-channels tracking.

    Verifies:
    - streams_merged counts individual merge operations (one per stream added).
    - channels_updated does NOT count merge operations.
    - _merge_streams_added_by_channel gives distinct-channels-touched.
    """

    def setup_method(self):
        self.client = MagicMock()
        self.client.update_channel = AsyncMock()
        # Two distinct channels; each will receive streams
        self.channels = [
            {"id": 10, "name": "ESPN", "tvg_id": "ESPN.US",
             "channel_number": 100, "streams": [], "auto_created": True},
            {"id": 20, "name": "CNN", "tvg_id": "CNN.US",
             "channel_number": 200, "streams": [], "auto_created": True},
        ]

    def _merge_stream_into(self, executor, channel_name, stream_id):
        action = {
            "type": "merge_streams",
            "target": "existing_channel",
            "find_channel_by": "name_exact",
            "find_channel_value": channel_name,
        }
        stream_ctx = StreamContext(
            stream_id=stream_id,
            stream_name=f"stream-{stream_id}",
            m3u_account_id=1,
        )
        exec_ctx = ExecutionContext()
        with patch("channel_pipeline_executor.journal.log_entries"):
            result = asyncio.get_event_loop().run_until_complete(
                executor.execute(action, stream_ctx, exec_ctx)
            )
        return result, exec_ctx

    def test_streams_merged_counts_individual_merge_operations(self):
        """streams_merged increments once per successfully added stream (bd-0emgo.4)."""
        # Old code: streams_merged was never incremented (dead counter).
        # New code: add_result with action_type="merge_stream" increments streams_merged.
        ctx = ExecutionContext()
        for i in range(5):
            ctx.add_result(ActionResult(
                success=True,
                action_type="merge_stream",
                description=f"Added stream {i}",
                entity_type="channel",
                entity_id=10,
                entity_name="ESPN",
                modified=True,
            ))
        assert ctx.streams_merged == 5
        assert ctx.channels_updated == 0, (
            "channels_updated must NOT be incremented by merge operations"
        )

    def test_merge_does_not_inflate_channels_updated(self):
        """Merging N streams into M channels: channels_updated==0, streams_merged==N (bd-0emgo.4)."""
        executor = ActionExecutor(self.client, existing_channels=self.channels)
        # 3 merges into ESPN (channel 10), 2 merges into CNN (channel 20) = 5 total
        merge_inputs = [
            ("ESPN", 301), ("ESPN", 302), ("ESPN", 303),
            ("CNN", 401), ("CNN", 402),
        ]
        total_streams_merged = 0
        total_channels_updated = 0
        for ch_name, sid in merge_inputs:
            _, exec_ctx = self._merge_stream_into(executor, ch_name, sid)
            total_streams_merged += exec_ctx.streams_merged
            total_channels_updated += exec_ctx.channels_updated

        assert total_channels_updated == 0, (
            "channels_updated inflated by merges (the bd-0emgo.4 bug is still present)"
        )
        assert total_streams_merged == 5

    def test_merge_streams_added_by_channel_gives_distinct_channel_count(self):
        """len(_merge_streams_added_by_channel) == number of distinct channels touched (bd-0emgo.4)."""
        executor = ActionExecutor(self.client, existing_channels=self.channels)
        # 3 merges into ESPN (id=10), 2 into CNN (id=20) — 2 distinct channels
        for sid in (301, 302, 303):
            self._merge_stream_into(executor, "ESPN", sid)
        for sid in (401, 402):
            self._merge_stream_into(executor, "CNN", sid)

        # Distinct channels touched = 2
        assert len(executor._merge_streams_added_by_channel) == 2
        assert 10 in executor._merge_streams_added_by_channel
        assert 20 in executor._merge_streams_added_by_channel
        # Stream IDs tracked per channel
        assert executor._merge_streams_added_by_channel[10] == {301, 302, 303}
        assert executor._merge_streams_added_by_channel[20] == {401, 402}

    def test_old_behavior_fails_without_fix(self):
        """Regression: prove the pre-fix behavior (channels_updated==N for merges) does NOT hold.

        If this assertion fires on the patched code, the fix has been reverted.
        """
        ctx = ExecutionContext()
        # Simulate 1341 merge operations into 726 distinct channels
        # (the production scenario from bd-0emgo.4).
        for i in range(1341):
            ctx.add_result(ActionResult(
                success=True,
                action_type="merge_stream",
                description=f"Merged stream {i}",
                entity_type="channel",
                entity_id=(i % 726) + 1,
                entity_name=f"Channel {(i % 726) + 1}",
                modified=True,
            ))
        # Pre-fix: channels_updated would have been 1341 (the bug).
        # Post-fix: channels_updated must be 0; streams_merged must be 1341.
        assert ctx.channels_updated != 1341, (
            "Pre-fix inflation bug is still present: channels_updated == streams count"
        )
        assert ctx.channels_updated == 0
        assert ctx.streams_merged == 1341


class TestChannelsTouchedDryRun:
    """bd-0emgo.4 dry-run consistency: channels_touched must match streams_merged.

    Live verification exposed: dry_run=True produced streams_merged==26 but
    channels_touched==0.

    Root cause: channels_touched was derived from a dict populated at scattered
    CALL SITES (_execute_merge_streams, and the _execute_create_channel
    if_exists=merge branch). That accounting missed merge paths, so the dict
    stayed empty while the ActionResult still carried action_type="merge_stream"
    and modified=True (so streams_merged incremented correctly) — channels_touched
    stayed 0.

    Fix (chokepoint): track distinct touched channels in
    ExecutionContext.merged_channel_ids, populated in ExecutionContext.add_result
    — the SINGLE point every merge_stream ActionResult flows through (merge_streams
    action AND create_channel if_exists=merge AND any future merge path), and the
    SAME point that counts streams_merged, so the two can never drift. The engine
    unions each stream's set into channels_touched. The prune dict
    _merge_streams_added_by_channel is reserved for merge_streams prune accounting
    and is no longer consulted for channels_touched.

    These tests mirror the engine: each stream gets its own ExecutionContext, and
    the test unions exec_ctx.merged_channel_ids across streams just as the engine's
    Pass-2 loop does.
    """

    def setup_method(self):
        self.client = MagicMock()
        self.client.update_channel = AsyncMock()
        # Three distinct channels; streams lists start empty.
        self.channels = [
            {"id": 10, "name": "ESPN", "tvg_id": "", "channel_number": 100, "streams": [], "auto_created": True},
            {"id": 20, "name": "CNN", "tvg_id": "", "channel_number": 200, "streams": [], "auto_created": True},
            {"id": 30, "name": "FOX", "tvg_id": "", "channel_number": 300, "streams": [], "auto_created": True},
        ]

    def _create_channel_merge_dry_run(self, executor, channel_name, stream_id,
                                      if_exists="merge"):
        """Execute a dry-run create_channel+if_exists=merge action for one stream.

        This is the code path that was MISSING the dict update: _execute_create_channel
        finds an existing channel → calls _add_stream_to_channel directly, bypassing
        _execute_merge_streams and its _merge_streams_added_by_channel update.
        """
        action = {
            "type": "create_channel",
            "name_template": channel_name,
            "if_exists": if_exists,
        }
        stream_ctx = StreamContext(
            stream_id=stream_id,
            stream_name=f"stream-{stream_id}",
            m3u_account_id=1,
        )
        exec_ctx = ExecutionContext(dry_run=True)
        result = asyncio.get_event_loop().run_until_complete(
            executor.execute(action, stream_ctx, exec_ctx)
        )
        return result, exec_ctx

    def _create_channel_merge_live(self, executor, channel_name, stream_id,
                                   if_exists="merge"):
        """Execute a live create_channel+if_exists=merge action for one stream."""
        action = {
            "type": "create_channel",
            "name_template": channel_name,
            "if_exists": if_exists,
        }
        stream_ctx = StreamContext(
            stream_id=stream_id,
            stream_name=f"stream-{stream_id}",
            m3u_account_id=1,
        )
        exec_ctx = ExecutionContext(dry_run=False)
        with patch("channel_pipeline_executor.journal.log_entries"):
            result = asyncio.get_event_loop().run_until_complete(
                executor.execute(action, stream_ctx, exec_ctx)
            )
        return result, exec_ctx

    # ------------------------------------------------------------------
    # DRY-RUN: create_channel + if_exists=merge (the reported bug path)
    # ------------------------------------------------------------------

    def test_create_channel_merge_dry_run_populates_dict(self):
        """DRY-RUN: create_channel+if_exists=merge records the touched channel.

        This is the exact bug: streams_merged==N but channels_touched==0.
        channels_touched is unioned from exec_ctx.merged_channel_ids (populated at
        the add_result chokepoint), which every merge path flows through.
        """
        executor = ActionExecutor(self.client, existing_channels=self.channels)

        # 3 merges into ESPN (id=10), 2 into CNN (id=20) — 2 distinct channels
        touched: set = set()
        for sid in (301, 302, 303):
            _, exec_ctx = self._create_channel_merge_dry_run(executor, "ESPN", sid)
            touched.update(exec_ctx.merged_channel_ids)
        for sid in (401, 402):
            _, exec_ctx = self._create_channel_merge_dry_run(executor, "CNN", sid)
            touched.update(exec_ctx.merged_channel_ids)

        assert len(touched) == 2, (
            f"channels_touched=0 (create_channel+if_exists=merge dry-run bug): "
            f"touched={touched!r}"
        )
        assert touched == {10, 20}

    def test_create_channel_merge_only_dry_run_populates_dict(self):
        """DRY-RUN: create_channel+if_exists=merge_only also records the channel."""
        executor = ActionExecutor(self.client, existing_channels=self.channels)

        touched: set = set()
        for sid in (501, 502):
            _, exec_ctx = self._create_channel_merge_dry_run(executor, "ESPN", sid, if_exists="merge_only")
            touched.update(exec_ctx.merged_channel_ids)
        _, exec_ctx = self._create_channel_merge_dry_run(executor, "FOX", 503, if_exists="merge_only")
        touched.update(exec_ctx.merged_channel_ids)

        assert len(touched) == 2, (
            f"merge_only dry-run bug: touched={touched!r}"
        )
        assert touched == {10, 30}

    def test_create_channel_merge_dry_run_streams_merged_channels_touched_consistent(self):
        """DRY-RUN: streams_merged and channels_touched are consistent for create_channel+merge.

        This is the exact inconsistency reported: streams_merged=26, channels_touched=0.
        After the fix: channels_touched == distinct target channels.
        """
        executor = ActionExecutor(self.client, existing_channels=self.channels)

        total_streams_merged = 0
        touched: set = set()
        inputs = [
            ("ESPN", 601), ("ESPN", 602), ("ESPN", 603),  # 3 into channel 10
            ("CNN",  701), ("CNN",  702),                  # 2 into channel 20
            ("FOX",  801),                                 # 1 into channel 30
        ]
        for ch_name, sid in inputs:
            _, exec_ctx = self._create_channel_merge_dry_run(executor, ch_name, sid)
            total_streams_merged += exec_ctx.streams_merged
            touched.update(exec_ctx.merged_channel_ids)

        assert total_streams_merged == 6, (
            f"streams_merged should be 6, got {total_streams_merged}"
        )
        channels_touched = len(touched)
        assert channels_touched == 3, (
            f"streams_merged={total_streams_merged} but channels_touched={channels_touched} "
            f"(inconsistency in dry-run create_channel+merge path)"
        )

    # ------------------------------------------------------------------
    # LIVE: create_channel + if_exists=merge (regression guard)
    # ------------------------------------------------------------------

    def test_create_channel_merge_live_populates_dict(self):
        """LIVE: create_channel+if_exists=merge also records the touched channel.

        Both live and dry-run paths must record the target channel id.
        """
        executor = ActionExecutor(self.client, existing_channels=self.channels)

        touched: set = set()
        for sid in (901, 902, 903):
            _, exec_ctx = self._create_channel_merge_live(executor, "ESPN", sid)
            touched.update(exec_ctx.merged_channel_ids)
        for sid in (911, 912):
            _, exec_ctx = self._create_channel_merge_live(executor, "CNN", sid)
            touched.update(exec_ctx.merged_channel_ids)

        assert len(touched) == 2
        assert touched == {10, 20}

    # ------------------------------------------------------------------
    # merge_streams action path (already worked; regression guard)
    # ------------------------------------------------------------------

    def test_merge_streams_action_dry_run_still_works(self):
        """REGRESSION: merge_streams action path still tracks correctly in dry-run.

        The fix must not break the merge_streams action path. It still populates
        the prune dict _merge_streams_added_by_channel (used by prune) AND now
        also records into exec_ctx.merged_channel_ids (used by channels_touched)
        via the add_result chokepoint.
        """
        executor = ActionExecutor(self.client, existing_channels=self.channels)

        touched: set = set()
        for sid in (1001, 1002):
            action = {
                "type": "merge_streams",
                "target": "existing_channel",
                "find_channel_by": "name_exact",
                "find_channel_value": "ESPN",
            }
            stream_ctx = StreamContext(stream_id=sid, stream_name=f"s{sid}", m3u_account_id=1)
            exec_ctx = ExecutionContext(dry_run=True)
            asyncio.get_event_loop().run_until_complete(
                executor.execute(action, stream_ctx, exec_ctx)
            )
            touched.update(exec_ctx.merged_channel_ids)

        # Prune dict (call-site accounting) still works:
        assert 10 in executor._merge_streams_added_by_channel
        assert executor._merge_streams_added_by_channel[10] == {1001, 1002}
        # Chokepoint set (channels_touched accounting) is populated too:
        assert touched == {10}


# ===========================================================================
# enhancedchannelmanager-orzck (W1): Manual-channel isolation
#
# Auto-creation matched a stream to an existing channel purely by NAME, with
# no auto_created filter. A rule with if_exists=merge (or a merge_streams
# action) could silently adopt a hand-built MANUAL channel (auto_created=False)
# as the merge/update/rename target when names collided — overwriting its
# name/metadata/filters. The fix gates _find_channel_by_name with
# block_manual=True so a protected manual channel yields "not found" and a new
# auto channel is created instead, UNLESS the firing rule opts in with
# allow_manual_channel_merge=True.
#
# CONVENTION (mirrors the ADR-010 snapshot precedent
# channel_pipeline_engine.py:_capture_snapshot — ``not ch.get("auto_created",
# False)``): a channel dict with a MISSING or falsy ``auto_created`` key is
# treated as MANUAL (protected). Only an explicit truthy ``auto_created`` makes
# a channel an auto-created merge candidate.
# ===========================================================================


def _make_manual_isolation_executor(extra_channels=None):
    """Executor with a MANUAL 'ESPN' channel (id=99, auto_created=False)."""
    client = MagicMock()
    _next = {"id": 900}

    async def _create_channel(data):
        _next["id"] += 1
        return {
            "id": _next["id"],
            "name": data["name"],
            "channel_number": data.get("channel_number"),
            "channel_group_id": data.get("channel_group_id"),
            "streams": data.get("streams", []),
        }

    client.create_channel = AsyncMock(side_effect=_create_channel)
    client.update_channel = AsyncMock(return_value={})
    client.get_channel = AsyncMock(return_value={"id": 99, "streams": [501]})

    channels = [
        {"id": 99, "name": "ESPN", "channel_number": 100,
         "channel_group_id": 1, "streams": [501], "auto_created": False},
    ]
    if extra_channels:
        channels.extend(extra_channels)
    groups = [{"id": 1, "name": "SPORTS"}, {"id": 2, "name": "ESPN-GROUP"}]
    executor = ActionExecutor(client, existing_channels=channels, existing_groups=groups)
    return client, executor


class TestManualChannelBleedRegression:
    """REPRODUCTION: create_channel if_exists=merge must not adopt a manual channel.

    Pre-fix: the stream merges into the manual ESPN (id=99) — update/merge is
    called against it (the user-reported bleed). Post-fix: the manual channel is
    UNTOUCHED and a brand-new auto channel is created instead.
    """

    def test_merge_does_not_touch_manual_channel(self):
        client, executor = _make_manual_isolation_executor()
        action = {
            "type": "create_channel",
            "name_template": "{stream_name}",
            "if_exists": "merge",
        }
        stream_ctx = StreamContext(stream_id=502, stream_name="ESPN", m3u_account_id=1)
        exec_ctx = ExecutionContext()

        result = asyncio.get_event_loop().run_until_complete(
            executor.execute(action, stream_ctx, exec_ctx, rule_target_group_id=1)
        )

        assert result.success is True
        # POST-FIX: manual channel id=99 is byte-identical — never updated/merged.
        for call in client.update_channel.call_args_list:
            assert call[0][0] != 99, "manual channel 99 was updated (bleed)"
        # add_stream_to_channel resolves streams via get_channel; assert the
        # manual channel was not the merge target.
        for call in client.get_channel.call_args_list:
            assert call[0][0] != 99, "stream was merged into manual channel 99 (bleed)"
        # Instead a NEW auto channel was created.
        client.create_channel.assert_called_once()
        created = client.create_channel.call_args[0][0]
        assert created["name"] == "ESPN"


class TestAutoCreatedFilter:
    """Matrix: the manual-channel gate across every name-resolution path."""

    # ---- create_channel if_exists=merge: gate blocks the manual channel ----

    def test_exact_name_merge_skips_manual_channel(self):
        client, executor = _make_manual_isolation_executor()
        action = {"type": "create_channel", "name_template": "{stream_name}",
                  "if_exists": "merge"}
        stream_ctx = StreamContext(stream_id=502, stream_name="ESPN", m3u_account_id=1)
        result = asyncio.get_event_loop().run_until_complete(
            executor.execute(action, stream_ctx, ExecutionContext(), rule_target_group_id=1)
        )
        assert result.success is True
        client.create_channel.assert_called_once()
        for call in client.get_channel.call_args_list:
            assert call[0][0] != 99

    def test_number_prefix_merge_skips_manual_channel(self):
        """Manual channel stored as '4000 | ESPN' (base-name map) is protected."""
        client = MagicMock()
        _next = {"id": 900}

        async def _create_channel(data):
            _next["id"] += 1
            return {"id": _next["id"], "name": data["name"],
                    "channel_group_id": data.get("channel_group_id"),
                    "streams": data.get("streams", [])}

        client.create_channel = AsyncMock(side_effect=_create_channel)
        client.update_channel = AsyncMock(return_value={})
        client.get_channel = AsyncMock(return_value={"id": 77, "streams": []})
        channels = [{"id": 77, "name": "4000 | ESPN", "channel_group_id": 1,
                     "streams": [], "auto_created": False}]
        executor = ActionExecutor(client, existing_channels=channels,
                                  existing_groups=[{"id": 1, "name": "SPORTS"}])
        action = {"type": "create_channel", "name_template": "{stream_name}",
                  "if_exists": "merge"}
        stream_ctx = StreamContext(stream_id=502, stream_name="ESPN", m3u_account_id=1)
        result = asyncio.get_event_loop().run_until_complete(
            executor.execute(action, stream_ctx, ExecutionContext(), rule_target_group_id=1)
        )
        assert result.success is True
        client.create_channel.assert_called_once()
        for call in client.update_channel.call_args_list:
            assert call[0][0] != 77

    def test_merge_streams_name_exact_skips_manual_channel(self):
        client, executor = _make_manual_isolation_executor()
        action = {"type": "merge_streams", "target": "existing_channel",
                  "find_channel_by": "name_exact", "find_channel_value": "ESPN"}
        stream_ctx = StreamContext(stream_id=502, stream_name="ESPN", m3u_account_id=1)
        result = asyncio.get_event_loop().run_until_complete(
            executor.execute(action, stream_ctx, ExecutionContext())
        )
        # No auto channel exists → merge_streams (existing only) yields no match.
        for call in client.get_channel.call_args_list:
            assert call[0][0] != 99
        assert result.skipped is True or result.success is False

    def test_merge_streams_tvg_id_skips_manual_channel(self):
        """tvg_id resolution path also rejects the manual channel."""
        client = MagicMock()
        client.create_channel = AsyncMock()
        client.update_channel = AsyncMock(return_value={})
        client.get_channel = AsyncMock(return_value={"id": 99, "streams": []})
        channels = [{"id": 99, "name": "ESPN", "channel_group_id": 1,
                     "streams": [], "tvg_id": "ESPN.us", "auto_created": False}]
        executor = ActionExecutor(client, existing_channels=channels,
                                  existing_groups=[{"id": 1, "name": "SPORTS"}])
        action = {"type": "merge_streams", "target": "existing_channel",
                  "find_channel_by": "tvg_id", "find_channel_value": "ESPN.us"}
        stream_ctx = StreamContext(stream_id=502, stream_name="ESPN", m3u_account_id=1,
                                   tvg_id="ESPN.us")
        result = asyncio.get_event_loop().run_until_complete(
            executor.execute(action, stream_ctx, ExecutionContext())
        )
        for call in client.get_channel.call_args_list:
            assert call[0][0] != 99
        assert result.skipped is True or result.success is False

    # ---- merge PROCEEDS for auto_created=True ----

    def test_merge_proceeds_for_auto_created_channel(self):
        """An auto_created=True channel IS a valid merge target."""
        client = MagicMock()
        client.create_channel = AsyncMock()
        client.update_channel = AsyncMock(return_value={})
        client.get_channel = AsyncMock(return_value={"id": 42, "streams": [600]})
        channels = [{"id": 42, "name": "ESPN", "channel_number": 100,
                     "channel_group_id": 1, "streams": [600], "auto_created": True}]
        executor = ActionExecutor(client, existing_channels=channels,
                                  existing_groups=[{"id": 1, "name": "SPORTS"}])
        action = {"type": "create_channel", "name_template": "{stream_name}",
                  "if_exists": "merge"}
        stream_ctx = StreamContext(stream_id=601, stream_name="ESPN", m3u_account_id=1)
        result = asyncio.get_event_loop().run_until_complete(
            executor.execute(action, stream_ctx, ExecutionContext(), rule_target_group_id=1)
        )
        assert result.success is True
        # Merged into the auto channel; no new channel created.
        client.create_channel.assert_not_called()
        assert any(call[0][0] == 42 for call in client.update_channel.call_args_list)

    # ---- opt-in: allow_manual_channel_merge=True DOES adopt the manual channel ----

    def test_opt_in_adopts_manual_channel(self):
        client, executor = _make_manual_isolation_executor()
        action = {"type": "create_channel", "name_template": "{stream_name}",
                  "if_exists": "merge"}
        stream_ctx = StreamContext(stream_id=502, stream_name="ESPN", m3u_account_id=1)
        result = asyncio.get_event_loop().run_until_complete(
            executor.execute(action, stream_ctx, ExecutionContext(),
                             rule_target_group_id=1,
                             allow_manual_channel_merge=True)
        )
        assert result.success is True
        # With opt-in, the merge DOES target the manual channel id=99.
        client.create_channel.assert_not_called()
        assert any(call[0][0] == 99 for call in client.update_channel.call_args_list)

    def test_opt_in_journals_adoption(self):
        """Opt-in adoption of a manual channel writes a journal entry."""
        client, executor = _make_manual_isolation_executor()
        # execution_id is required for the executor to write journal entries.
        executor._execution_id = 12345
        action = {"type": "create_channel", "name_template": "{stream_name}",
                  "if_exists": "merge"}
        stream_ctx = StreamContext(stream_id=502, stream_name="ESPN", m3u_account_id=1)
        asyncio.get_event_loop().run_until_complete(
            executor.execute(action, stream_ctx, ExecutionContext(),
                             rule_target_group_id=1,
                             allow_manual_channel_merge=True)
        )
        adoption_entries = [
            e for e in executor._journal_buffer
            if e.get("action_type") == "manual_channel_adopted"
        ]
        assert len(adoption_entries) == 1
        assert adoption_entries[0]["entity_id"] == 99

    # ---- gate parameter on the chokepoint itself ----

    def test_find_channel_by_name_block_manual_default_blocks(self):
        _client, executor = _make_manual_isolation_executor()
        # Default block_manual=True → manual channel is "not found".
        assert executor._find_channel_by_name("ESPN") is None

    def test_find_channel_by_name_block_manual_false_returns(self):
        _client, executor = _make_manual_isolation_executor()
        cand = executor._find_channel_by_name("ESPN", block_manual=False)
        assert cand is not None and cand["id"] == 99

    def test_missing_auto_created_key_treated_as_manual(self):
        """A channel dict with NO auto_created key is protected (manual)."""
        client = MagicMock()
        channels = [{"id": 55, "name": "ESPN", "channel_group_id": 1, "streams": []}]
        executor = ActionExecutor(client, existing_channels=channels,
                                  existing_groups=[{"id": 1, "name": "SPORTS"}])
        assert executor._find_channel_by_name("ESPN") is None
        assert executor._find_channel_by_name("ESPN", block_manual=False)["id"] == 55


class TestManualChannelBlockVisibility:
    """enhancedchannelmanager-wy6l5: a manual-channel-blocked merge must be a
    user-visible outcome (execution-log description + journal entry), not an
    INFO-log-only event.

    Mirrors the opt-in ``manual_channel_adopted`` journal pattern: the blocked
    path writes a ``manual_channel_merge_blocked`` entry (deduped to one per
    blocked channel per run) and the ActionResult description/details name the
    blocked manual channel so the executions list / debug bundle show WHY the
    rule "skipped everything" — and that a duplicate auto channel may be
    created instead.
    """

    # ---- merge_streams: blocked reason lands in the ActionResult ----

    def test_merge_streams_name_exact_blocked_reason_in_description(self):
        _client, executor = _make_manual_isolation_executor()
        action = {"type": "merge_streams", "target": "existing_channel",
                  "find_channel_by": "name_exact", "find_channel_value": "ESPN"}
        stream_ctx = StreamContext(stream_id=502, stream_name="ESPN", m3u_account_id=1)
        result = asyncio.get_event_loop().run_until_complete(
            executor.execute(action, stream_ctx, ExecutionContext())
        )
        # Same failure semantics as "not found" (success=False), but the
        # description must name the blocked manual channel and the flag.
        assert result.success is False
        assert "ESPN" in result.description
        assert "id=99" in result.description
        assert "allow_manual_channel_merge" in result.description
        assert result.entity_id == 99

    def test_merge_streams_auto_blocked_reason_in_description(self):
        _client, executor = _make_manual_isolation_executor()
        action = {"type": "merge_streams", "target": "auto"}
        stream_ctx = StreamContext(stream_id=502, stream_name="ESPN", m3u_account_id=1)
        result = asyncio.get_event_loop().run_until_complete(
            executor.execute(action, stream_ctx, ExecutionContext())
        )
        # auto target with no match is a skip — but the reason must be the
        # manual block, not the generic "no existing channel found".
        assert result.skipped is True
        assert "ESPN" in result.description
        assert "id=99" in result.description
        assert "allow_manual_channel_merge" in result.description

    def test_merge_streams_tvg_id_blocked_journals_and_describes(self):
        """The post-resolution reject (non-chokepoint paths) is also covered."""
        client = MagicMock()
        client.create_channel = AsyncMock()
        client.update_channel = AsyncMock(return_value={})
        client.get_channel = AsyncMock(return_value={"id": 99, "streams": []})
        channels = [{"id": 99, "name": "ESPN", "channel_group_id": 1,
                     "streams": [], "tvg_id": "ESPN.us", "auto_created": False}]
        executor = ActionExecutor(client, existing_channels=channels,
                                  existing_groups=[{"id": 1, "name": "SPORTS"}])
        executor._execution_id = 777
        action = {"type": "merge_streams", "target": "existing_channel",
                  "find_channel_by": "tvg_id", "find_channel_value": "ESPN.us"}
        stream_ctx = StreamContext(stream_id=502, stream_name="ESPN", m3u_account_id=1,
                                   tvg_id="ESPN.us")
        result = asyncio.get_event_loop().run_until_complete(
            executor.execute(action, stream_ctx, ExecutionContext())
        )
        assert "allow_manual_channel_merge" in result.description
        blocked = [e for e in executor._journal_buffer
                   if e.get("action_type") == "manual_channel_merge_blocked"]
        assert len(blocked) == 1
        assert blocked[0]["entity_id"] == 99
        assert "ESPN" in blocked[0]["description"]

    # ---- create_channel if_exists=merge: consequence is discoverable ----

    def test_create_channel_blocked_detail_and_journal(self):
        client, executor = _make_manual_isolation_executor()
        executor._execution_id = 12345
        action = {"type": "create_channel", "name_template": "{stream_name}",
                  "if_exists": "merge"}
        stream_ctx = StreamContext(stream_id=502, stream_name="ESPN", m3u_account_id=1)
        result = asyncio.get_event_loop().run_until_complete(
            executor.execute(action, stream_ctx, ExecutionContext(), rule_target_group_id=1)
        )
        # Behavior unchanged: a NEW auto channel is created...
        assert result.success is True
        client.create_channel.assert_called_once()
        # ...but the block and its consequence are now user-visible details.
        joined = " ".join(result.details)
        assert "allow_manual_channel_merge" in joined
        assert "id=99" in joined
        blocked = [e for e in executor._journal_buffer
                   if e.get("action_type") == "manual_channel_merge_blocked"]
        assert len(blocked) == 1
        assert blocked[0]["entity_id"] == 99

    # ---- journal hygiene ----

    def test_block_journal_dedupes_per_channel_per_run(self):
        _client, executor = _make_manual_isolation_executor()
        executor._execution_id = 12345
        action = {"type": "merge_streams", "target": "auto"}
        for stream_id in (502, 503):
            stream_ctx = StreamContext(stream_id=stream_id, stream_name="ESPN",
                                       m3u_account_id=1)
            asyncio.get_event_loop().run_until_complete(
                executor.execute(action, stream_ctx, ExecutionContext())
            )
        blocked = [e for e in executor._journal_buffer
                   if e.get("action_type") == "manual_channel_merge_blocked"]
        assert len(blocked) == 1

    def test_no_block_journal_without_execution_id(self):
        _client, executor = _make_manual_isolation_executor()
        # _execution_id stays None (direct-construct/tests) → journaling off,
        # same contract as _journal_manual_channel_adoption.
        action = {"type": "merge_streams", "target": "auto"}
        stream_ctx = StreamContext(stream_id=502, stream_name="ESPN", m3u_account_id=1)
        asyncio.get_event_loop().run_until_complete(
            executor.execute(action, stream_ctx, ExecutionContext())
        )
        assert executor._journal_buffer == []

    def test_opt_in_writes_adoption_not_block(self):
        client, executor = _make_manual_isolation_executor()
        executor._execution_id = 12345
        action = {"type": "create_channel", "name_template": "{stream_name}",
                  "if_exists": "merge"}
        stream_ctx = StreamContext(stream_id=502, stream_name="ESPN", m3u_account_id=1)
        asyncio.get_event_loop().run_until_complete(
            executor.execute(action, stream_ctx, ExecutionContext(),
                             rule_target_group_id=1,
                             allow_manual_channel_merge=True)
        )
        kinds = {e.get("action_type") for e in executor._journal_buffer}
        assert "manual_channel_adopted" in kinds
        assert "manual_channel_merge_blocked" not in kinds


def _make_name_transform_executor():
    """Executor with no existing channels/groups for name-transform tests."""
    client = MagicMock()
    _next = {"id": 900}

    async def _create_channel(data):
        _next["id"] += 1
        return {
            "id": _next["id"],
            "name": data["name"],
            "channel_number": data.get("channel_number"),
            "channel_group_id": data.get("channel_group_id"),
            "streams": data.get("streams", []),
        }

    client.create_channel = AsyncMock(side_effect=_create_channel)
    client.create_channel_group = AsyncMock(
        return_value={"id": 77, "name": "created-group"})
    client.update_channel = AsyncMock(return_value={})
    executor = ActionExecutor(
        client, existing_channels=[],
        existing_groups=[{"id": 1, "name": "SPORTS"}])
    return client, executor


class TestNameTransformFailureVisibility:
    """enhancedchannelmanager-3gigl: a name-transform regex failure at
    execution time (invalid group reference, timeout, oversize) must be a
    user-visible outcome — an entry in the ActionResult details (execution
    log / rule Test output) and a deduped journal entry — not a
    hash-labeled safe_regex WARNING only.

    The regression scenario is the user-reported one: a pre-existing
    (grandfathered) rule with a 3-group pattern and a '$2 $1 $3 $4'
    replacement. The regex library raises lazily — only on matching
    inputs — and safe_regex swallows into a per-stream no-op.
    """

    PATTERN = r"(\w+) (\w+) (\w+)"
    BAD_REPLACEMENT = "$2 $1 $3 $4"

    # ---- _apply_name_transform reports through _last_name_transform_error ----

    def test_apply_name_transform_failure_stamps_last_error(self):
        _client, executor = _make_name_transform_executor()
        out = executor._apply_name_transform(
            "ESPN Sports HD",
            {"name_transform_pattern": self.PATTERN,
             "name_transform_replacement": self.BAD_REPLACEMENT},
        )
        # Behavior unchanged: the name passes through untransformed.
        assert out == "ESPN Sports HD"
        err = executor._last_name_transform_error
        assert err is not None
        assert "invalid group reference" in err
        assert self.PATTERN in err
        assert self.BAD_REPLACEMENT in err

    def test_apply_name_transform_success_resets_last_error(self):
        _client, executor = _make_name_transform_executor()
        executor._apply_name_transform(
            "ESPN Sports HD",
            {"name_transform_pattern": self.PATTERN,
             "name_transform_replacement": self.BAD_REPLACEMENT},
        )
        assert executor._last_name_transform_error is not None
        out = executor._apply_name_transform(
            "ESPN HD",
            {"name_transform_pattern": r"\s*HD$",
             "name_transform_replacement": ""},
        )
        assert out == "ESPN"
        assert executor._last_name_transform_error is None

    def test_non_matching_input_is_not_a_failure(self):
        """The regex engine parses the template lazily — a stream the
        pattern does not match must not report a failure."""
        _client, executor = _make_name_transform_executor()
        out = executor._apply_name_transform(
            "ESPN",
            {"name_transform_pattern": self.PATTERN,
             "name_transform_replacement": self.BAD_REPLACEMENT},
        )
        assert out == "ESPN"
        assert executor._last_name_transform_error is None

    # ---- failure surfaces in the execution log via ActionResult.details ----

    def test_create_channel_details_surface_transform_failure(self):
        client, executor = _make_name_transform_executor()
        action = {"type": "create_channel", "name_template": "{stream_name}",
                  "name_transform_pattern": self.PATTERN,
                  "name_transform_replacement": self.BAD_REPLACEMENT}
        stream_ctx = StreamContext(stream_id=502, stream_name="ESPN Sports HD",
                                   m3u_account_id=1)
        result = asyncio.get_event_loop().run_until_complete(
            executor.execute(action, stream_ctx, ExecutionContext(),
                             rule_target_group_id=1)
        )
        # Behavior unchanged: channel still created with the untransformed name.
        assert result.success is True
        client.create_channel.assert_called_once()
        joined = " ".join(result.details)
        assert "Name transform failed" in joined
        assert "invalid group reference" in joined

    def test_create_group_details_surface_transform_failure(self):
        client, executor = _make_name_transform_executor()
        action = {"type": "create_group", "name_template": "{stream_group}",
                  "name_transform_pattern": self.PATTERN,
                  "name_transform_replacement": self.BAD_REPLACEMENT}
        stream_ctx = StreamContext(stream_id=502, stream_name="ESPN",
                                   m3u_account_id=1,
                                   group_name="US Sports East")
        result = asyncio.get_event_loop().run_until_complete(
            executor.execute(action, stream_ctx, ExecutionContext())
        )
        assert result.success is True
        joined = " ".join(result.details)
        assert "Name transform failed" in joined
        assert "invalid group reference" in joined

    def test_no_failure_detail_when_transform_succeeds(self):
        client, executor = _make_name_transform_executor()
        action = {"type": "create_channel", "name_template": "{stream_name}",
                  "name_transform_pattern": r"\s*HD$",
                  "name_transform_replacement": ""}
        stream_ctx = StreamContext(stream_id=502, stream_name="ESPN HD",
                                   m3u_account_id=1)
        result = asyncio.get_event_loop().run_until_complete(
            executor.execute(action, stream_ctx, ExecutionContext(),
                             rule_target_group_id=1)
        )
        assert result.success is True
        assert all("Name transform failed" not in d for d in result.details)

    def test_dry_run_details_surface_transform_failure(self):
        """Rule Test / dry-run output carries the same failure detail."""
        _client, executor = _make_name_transform_executor()
        action = {"type": "create_channel", "name_template": "{stream_name}",
                  "name_transform_pattern": self.PATTERN,
                  "name_transform_replacement": self.BAD_REPLACEMENT}
        stream_ctx = StreamContext(stream_id=502, stream_name="ESPN Sports HD",
                                   m3u_account_id=1)
        result = asyncio.get_event_loop().run_until_complete(
            executor.execute(action, stream_ctx, ExecutionContext(dry_run=True),
                             rule_target_group_id=1)
        )
        joined = " ".join(result.details)
        assert "Name transform failed" in joined

    # ---- journal: one entry per (pattern, replacement, kind) per run ----

    def test_transform_failure_journaled_once_per_rule_per_run(self):
        _client, executor = _make_name_transform_executor()
        executor._execution_id = 4242
        action = {"type": "create_channel", "name_template": "{stream_name}",
                  "name_transform_pattern": self.PATTERN,
                  "name_transform_replacement": self.BAD_REPLACEMENT}
        for stream_id, name in ((502, "ESPN Sports HD"), (503, "Fox Sports One")):
            stream_ctx = StreamContext(stream_id=stream_id, stream_name=name,
                                       m3u_account_id=1)
            asyncio.get_event_loop().run_until_complete(
                executor.execute(action, stream_ctx, ExecutionContext(),
                                 rule_target_group_id=1)
            )
        entries = [e for e in executor._journal_buffer
                   if e.get("action_type") == "name_transform_failed"]
        assert len(entries) == 1
        assert "invalid group reference" in entries[0]["description"]
        assert entries[0]["batch_id"] == "4242"

    def test_no_transform_failure_journal_without_execution_id(self):
        _client, executor = _make_name_transform_executor()
        action = {"type": "create_channel", "name_template": "{stream_name}",
                  "name_transform_pattern": self.PATTERN,
                  "name_transform_replacement": self.BAD_REPLACEMENT}
        stream_ctx = StreamContext(stream_id=502, stream_name="ESPN Sports HD",
                                   m3u_account_id=1)
        asyncio.get_event_loop().run_until_complete(
            executor.execute(action, stream_ctx, ExecutionContext(),
                             rule_target_group_id=1)
        )
        assert executor._journal_buffer == []


class TestCreateChannelFoldMatchKey:
    """GH #645 / bead enhancedchannelmanager-0vao3: opt-in ``fold_match_key``.

    When a rule opts in, the create_channel ``if_exists`` merge lookup
    compares names by a canonicalized key (casefold + strip ALL whitespace,
    via the shared ``match_fold.fold_match_key`` helper) so spellings like
    "eurosport 2" / "Eurosport 2" / "Eurosport2" / "eurosport2" land in ONE
    channel. Comparison key ONLY — the stored/visible channel name is never
    altered. Default (flag off) preserves the current behavior exactly.
    """

    # The exact four spellings from the GH #645 report.
    REPORTED_NAMES = ["eurosport 2", "Eurosport 2", "Eurosport2", "eurosport2"]

    def _make_client(self):
        client = MagicMock()
        counter = {"next": 1000}

        async def _create_channel(data):
            counter["next"] += 1
            return dict(data, id=counter["next"])

        client.create_channel = AsyncMock(side_effect=_create_channel)
        client.update_channel = AsyncMock(return_value={})
        client.get_channel = AsyncMock(return_value={"streams": []})
        return client

    def _run(self, executor, names, fold_match_key, dry_run=False):
        """Feed streams named ``names`` through create_channel if_exists=merge."""
        action = {
            "type": "create_channel",
            "name_template": "{stream_name}",
            "if_exists": "merge",
        }
        created, merged = [], []
        for i, name in enumerate(names):
            stream_ctx = StreamContext(stream_id=100 + i, stream_name=name,
                                       m3u_account_id=1)
            result = asyncio.get_event_loop().run_until_complete(
                executor.execute(action, stream_ctx, ExecutionContext(dry_run=dry_run),
                                 fold_match_key=fold_match_key)
            )
            assert result.success is True
            (created if result.created else merged).append(name)
        return created, merged

    def test_golden_reported_names_fold_on_one_channel(self):
        """Flag ON: the four reported spellings produce exactly ONE channel."""
        client = self._make_client()
        executor = ActionExecutor(client, existing_channels=[])

        created, merged = self._run(executor, self.REPORTED_NAMES, fold_match_key=True)

        assert created == ["eurosport 2"]
        assert merged == ["Eurosport 2", "Eurosport2", "eurosport2"]
        # The stored name is the FIRST spelling seen — never rewritten.
        client.create_channel.assert_called_once()
        assert client.create_channel.call_args[0][0]["name"] == "eurosport 2"

    def test_golden_reported_names_flag_off_preserves_current_behavior(self):
        """Flag OFF (default): case-only pairs merge, whitespace pairs do NOT
        (two channels) — the pre-flag behavior, pinned."""
        client = self._make_client()
        executor = ActionExecutor(client, existing_channels=[])

        created, merged = self._run(executor, self.REPORTED_NAMES, fold_match_key=False)

        assert created == ["eurosport 2", "Eurosport2"]
        assert merged == ["Eurosport 2", "eurosport2"]

    def test_fold_matches_existing_channel_from_previous_run(self):
        """Second-run topology: existing channel 'eurosport 2', incoming
        'Eurosport2' with the flag ON merges instead of creating."""
        client = self._make_client()
        existing = [{"id": 7, "name": "eurosport 2", "streams": [],
                     "auto_created": True}]
        executor = ActionExecutor(client, existing_channels=existing)

        created, merged = self._run(executor, ["Eurosport2"], fold_match_key=True)

        assert created == []
        assert merged == ["Eurosport2"]
        client.create_channel.assert_not_called()

    def test_fold_matches_number_prefixed_existing_channel(self):
        """The fold also folds the number-prefix-stripped base name of an
        existing channel ('4000 | Euro Sport' matches 'EuroSport')."""
        client = self._make_client()
        existing = [{"id": 8, "name": "4000 | Euro Sport", "streams": [],
                     "auto_created": True}]
        executor = ActionExecutor(client, existing_channels=existing)

        created, merged = self._run(executor, ["EuroSport"], fold_match_key=True)

        assert created == []
        assert merged == ["EuroSport"]
        client.create_channel.assert_not_called()

    def test_fold_does_not_over_merge_distinct_names(self):
        """Flag ON must not merge names that differ beyond whitespace/case."""
        client = self._make_client()
        executor = ActionExecutor(client, existing_channels=[])

        names = ["Eurosport 2", "Eurosport 3", "Eurosport 2 HD", "ESPN", "ESPN2"]
        created, merged = self._run(executor, names, fold_match_key=True)

        assert created == names
        assert merged == []

    def test_fold_dry_run_dedupes_like_execute(self):
        """Dry-run preview with the flag ON simulates the same single channel."""
        client = self._make_client()
        executor = ActionExecutor(client, existing_channels=[])

        created, merged = self._run(executor, self.REPORTED_NAMES,
                                    fold_match_key=True, dry_run=True)

        assert created == ["eurosport 2"]
        assert merged == ["Eurosport 2", "Eurosport2", "eurosport2"]
        client.create_channel.assert_not_called()

    def test_fold_respects_group_scope(self):
        """A folded match in a DIFFERENT group is still rejected when
        match_scope_target_group is on (scope wins over fold)."""
        client = self._make_client()
        existing = [{"id": 9, "name": "Eurosport2", "streams": [],
                     "auto_created": True, "channel_group_id": 7}]
        executor = ActionExecutor(client, existing_channels=existing)

        action = {"type": "create_channel", "name_template": "{stream_name}",
                  "if_exists": "merge", "group_id": 9}
        stream_ctx = StreamContext(stream_id=100, stream_name="Eurosport 2",
                                   m3u_account_id=1)
        result = asyncio.get_event_loop().run_until_complete(
            executor.execute(action, stream_ctx, ExecutionContext(),
                             match_scope_target_group=True,
                             fold_match_key=True)
        )

        assert result.success is True
        assert result.created is True
        client.create_channel.assert_called_once()

    def test_fold_respects_manual_channel_gate(self):
        """A folded match on a MANUAL channel is still blocked when
        allow_manual_channel_merge is off (manual gate wins over fold)."""
        client = self._make_client()
        existing = [{"id": 10, "name": "Eurosport2", "streams": [],
                     "auto_created": False}]
        executor = ActionExecutor(client, existing_channels=existing)

        created, merged = self._run(executor, ["Eurosport 2"], fold_match_key=True)

        assert created == ["Eurosport 2"]
        assert merged == []
        # The manual channel was never touched.
        for call in client.update_channel.call_args_list:
            assert call[0][0] != 10

    def test_exact_match_wins_over_folded_match(self):
        """When both an exact and a folded candidate exist, the exact one is
        chosen — the fold is a fallback, not a replacement."""
        client = self._make_client()
        existing = [
            {"id": 11, "name": "Eurosport 2", "streams": [], "auto_created": True},
            {"id": 12, "name": "Eurosport2", "streams": [], "auto_created": True},
        ]
        executor = ActionExecutor(client, existing_channels=existing)

        found = executor._find_channel_by_name("eurosport 2", fold_key=True)
        assert found is not None
        assert found["id"] == 11


class TestAssignChannelProfileAction:
    """Tests for the assign_channel_profile action (GH #720 / y3m6o).

    Dispatcharr auto-joins every newly-created channel to ALL channel
    profiles, so honoring a per-rule profile selection is SUBTRACTIVE: the
    selected profiles must be enabled AND every other known profile disabled.
    The pre-fix enable-only loop was a no-op — the channel stayed in every
    profile. These tests lock in the exclusive-membership behavior.
    """

    def setup_method(self):
        self.client = MagicMock()
        self.client.update_profile_channel = AsyncMock()

        self.stream_ctx = StreamContext(
            stream_id=201,
            stream_name="ESPN2 HD",
            m3u_account_id=1,
            m3u_account_name="Provider A",
            group_name="Sports",
        )

    def _make_executor(self, all_profile_ids):
        return ActionExecutor(self.client, all_profile_ids=all_profile_ids)

    def _run(self, executor, action, exec_ctx):
        return asyncio.get_event_loop().run_until_complete(
            executor.execute(action, self.stream_ctx, exec_ctx)
        )

    @staticmethod
    def _calls(client):
        """Return {profile_id: enabled} from every update_profile_channel call."""
        out = {}
        for call in client.update_profile_channel.call_args_list:
            pid = call.args[0]
            body = call.args[2]
            out[pid] = body["enabled"]
        return out

    def test_selecting_one_profile_disables_the_rest(self):
        """The #720 regression: selecting profile 1 out of {1,2,3} must ENABLE 1
        and DISABLE 2 and 3 — not enable-only."""
        executor = self._make_executor(all_profile_ids=[1, 2, 3])
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 99
        action = {"type": "assign_channel_profile", "channel_profile_ids": [1]}

        result = self._run(executor, action, exec_ctx)

        assert result.success is True
        assert self._calls(self.client) == {1: True, 2: False, 3: False}

    def test_selecting_multiple_profiles(self):
        """Enabling a subset disables the complement."""
        executor = self._make_executor(all_profile_ids=[1, 2, 3, 4])
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 99
        action = {"type": "assign_channel_profile", "channel_profile_ids": [1, 3]}

        result = self._run(executor, action, exec_ctx)

        assert result.success is True
        assert self._calls(self.client) == {1: True, 2: False, 3: True, 4: False}

    def test_selected_profile_outside_known_universe_still_enabled(self):
        """A selected profile id missing from a stale fetched list is still
        (re)enabled — the union order guarantees selections are honored."""
        executor = self._make_executor(all_profile_ids=[1, 2])
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 99
        action = {"type": "assign_channel_profile", "channel_profile_ids": [5]}

        result = self._run(executor, action, exec_ctx)

        assert result.success is True
        # 5 enabled (from selection), 1 and 2 disabled (known universe).
        assert self._calls(self.client) == {5: True, 1: False, 2: False}

    def test_genuinely_empty_universe_degrades_to_enable_selected_only(self):
        """A GENUINELY-empty universe ([] — zero profiles configured, a known
        fact) enables the selected profiles and performs no disables. This is a
        real, known universe, distinct from an UNAVAILABLE one (None), so it
        still reports success — never worse than the pre-fix behavior."""
        executor = self._make_executor(all_profile_ids=[])
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 99
        action = {"type": "assign_channel_profile", "channel_profile_ids": [1, 2]}

        result = self._run(executor, action, exec_ctx)

        assert result.success is True
        assert self._calls(self.client) == {1: True, 2: True}

    def test_unavailable_universe_fails_and_makes_no_calls(self):
        """Bug 2 regression: an UNAVAILABLE universe (None sentinel — the engine
        could not fetch the profile list) must NOT silently degrade to
        enable-only-and-report-success. Exclusive membership is unprovable, so
        the action FAILS and performs no profile writes (GH #720 / y3m6o.1)."""
        executor = self._make_executor(all_profile_ids=None)
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 99
        action = {"type": "assign_channel_profile", "channel_profile_ids": [1, 2]}

        result = self._run(executor, action, exec_ctx)

        assert result.success is False
        assert result.modified is False
        assert "unavailable" in (result.error or "").lower()
        self.client.update_profile_channel.assert_not_called()

    def test_default_construct_without_universe_fails(self):
        """Constructing the executor WITHOUT a profile universe leaves the
        universe unknown (None) — assign_channel_profile cannot prove
        exclusivity and must fail rather than enable-only."""
        executor = ActionExecutor(self.client)  # no all_profile_ids
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 99
        action = {"type": "assign_channel_profile", "channel_profile_ids": [1]}

        result = self._run(executor, action, exec_ctx)

        assert result.success is False
        self.client.update_profile_channel.assert_not_called()

    def test_dry_run_makes_no_client_calls(self):
        executor = self._make_executor(all_profile_ids=[1, 2, 3])
        exec_ctx = ExecutionContext(dry_run=True)
        exec_ctx.current_channel_id = 99
        action = {"type": "assign_channel_profile", "channel_profile_ids": [1]}

        result = self._run(executor, action, exec_ctx)

        assert result.success is True
        # Exclusive-semantics dry-run wording (y3m6o.2): the preview names BOTH
        # halves — the enabled/selected impact AND the subtractive removal from
        # every other profile.
        assert "EXCLUSIVE" in result.description
        assert "REMOVE from all" in result.description
        self.client.update_profile_channel.assert_not_called()

    def test_no_channel_context_fails(self):
        executor = self._make_executor(all_profile_ids=[1, 2, 3])
        exec_ctx = ExecutionContext()  # no current_channel_id
        action = {"type": "assign_channel_profile", "channel_profile_ids": [1]}

        result = self._run(executor, action, exec_ctx)

        assert result.success is False
        self.client.update_profile_channel.assert_not_called()

    def test_empty_channel_profile_ids_fails(self):
        executor = self._make_executor(all_profile_ids=[1, 2, 3])
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 99
        action = {"type": "assign_channel_profile", "channel_profile_ids": []}

        result = self._run(executor, action, exec_ctx)

        assert result.success is False
        self.client.update_profile_channel.assert_not_called()

    def test_failing_disable_does_not_abort_but_reports_partial_failure(self):
        """Bug 1 regression: a per-profile DISABLE failure is best-effort
        continued (every other profile is still attempted) BUT the action no
        longer reports success — the failed profile id is surfaced so the
        incomplete reconciliation is observable and retryable (GH #720)."""
        def side_effect(pid, channel_id, body):
            if pid == 2:
                raise RuntimeError("boom")
            return None
        self.client.update_profile_channel = AsyncMock(side_effect=side_effect)

        executor = self._make_executor(all_profile_ids=[1, 2, 3])
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 99
        action = {"type": "assign_channel_profile", "channel_profile_ids": [1]}

        result = self._run(executor, action, exec_ctx)

        # Best-effort continuation: profiles 1, 2, and 3 were all attempted.
        attempted = {c.args[0] for c in self.client.update_profile_channel.call_args_list}
        assert {1, 2, 3} == attempted
        # But exclusivity is UNPROVEN — the disable of profile 2 failed.
        assert result.success is False
        assert "2" in (result.error or "")

    def test_failing_enable_reports_partial_failure(self):
        """Bug 1 regression: a failing ENABLE (of a selected profile) likewise
        yields a non-success result surfacing the failed id."""
        def side_effect(pid, channel_id, body):
            if pid == 1:  # 1 is the SELECTED (enable) profile
                raise RuntimeError("enable boom")
            return None
        self.client.update_profile_channel = AsyncMock(side_effect=side_effect)

        executor = self._make_executor(all_profile_ids=[1, 2, 3])
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 99
        action = {"type": "assign_channel_profile", "channel_profile_ids": [1]}

        result = self._run(executor, action, exec_ctx)

        assert result.success is False
        assert "1" in (result.error or "")
        # Still modified — the successful disables of 2 and 3 did land.
        assert result.modified is True

    def test_total_failure_reports_not_success(self):
        """Bug 1 regression: when EVERY profile update fails the action reports
        failure with all failed ids surfaced."""
        self.client.update_profile_channel = AsyncMock(
            side_effect=RuntimeError("all boom")
        )

        executor = self._make_executor(all_profile_ids=[1, 2, 3])
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 99
        action = {"type": "assign_channel_profile", "channel_profile_ids": [1]}

        result = self._run(executor, action, exec_ctx)

        assert result.success is False
        for pid in ("1", "2", "3"):
            assert pid in (result.error or "")

    def test_all_succeed_reports_success_with_counts(self):
        """The happy path is unchanged: all profile updates succeed => success
        with correct enabled/disabled counts in the description."""
        executor = self._make_executor(all_profile_ids=[1, 2, 3, 4])
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 99
        action = {"type": "assign_channel_profile", "channel_profile_ids": [1, 2]}

        result = self._run(executor, action, exec_ctx)

        assert result.success is True
        assert "enabled in 2" in result.description
        assert "disabled in 2" in result.description

    def test_helper_none_universe_coerces_to_empty_enables_selected_only(self):
        """Nit #2(a): pin the defensive None -> [] coercion INSIDE the helper.

        Callers gate on availability before invoking the helper, so it is never
        reached with None in practice — but if it is, it must not crash: it
        coerces the unavailable universe to an empty one, enables the selected
        profiles only (nothing to disable), and returns a ProfileMembershipResult
        with no failures."""
        from channel_pipeline_executor import ProfileMembershipResult

        executor = self._make_executor(all_profile_ids=None)
        result = asyncio.get_event_loop().run_until_complete(
            executor._apply_exclusive_profile_membership(99, [1, 2])
        )

        assert isinstance(result, ProfileMembershipResult)
        assert result.enabled_count == 2
        assert result.disabled_count == 0
        assert result.failed_profile_ids == []
        assert self._calls(self.client) == {1: True, 2: True}

    def test_both_enable_and_disable_failing_surfaces_all_failed_ids(self):
        """Nit #2(b): a mixed failure where BOTH an enable (selected id 1) and a
        disable (unselected id 3) raise in the same invocation — both ids appear
        in the failure surface and the action reports non-success."""
        def side_effect(pid, channel_id, body):
            if pid in (1, 3):  # 1 = selected (enable), 3 = unselected (disable)
                raise RuntimeError("boom")
            return None
        self.client.update_profile_channel = AsyncMock(side_effect=side_effect)

        executor = self._make_executor(all_profile_ids=[1, 2, 3])
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 99
        action = {"type": "assign_channel_profile", "channel_profile_ids": [1]}

        result = self._run(executor, action, exec_ctx)

        assert result.success is False
        assert "1" in (result.error or "")
        assert "3" in (result.error or "")
        # All three were still attempted (best-effort continuation).
        attempted = {c.args[0] for c in self.client.update_profile_channel.call_args_list}
        assert {1, 2, 3} == attempted


    def test_dry_run_unavailable_universe_blocks_preview(self):
        """y3m6o.1 Finding 2 (0152): a DRY RUN whose profile universe is
        unavailable (None) must NOT preview a rosy 'Would assign N…' — the live
        run cannot honor it. It returns an explicit blocking outcome
        (success=False, modified=False) and makes no client calls, in dry-run
        exactly as in a live run."""
        executor = self._make_executor(all_profile_ids=None)
        exec_ctx = ExecutionContext(dry_run=True)
        exec_ctx.current_channel_id = 99
        action = {"type": "assign_channel_profile", "channel_profile_ids": [1]}

        result = self._run(executor, action, exec_ctx)

        assert result.success is False
        assert result.modified is False
        assert "unavailable" in (result.error or "").lower()
        assert "Would assign" not in result.description
        self.client.update_profile_channel.assert_not_called()

    def test_dry_run_known_universe_still_previews(self):
        """Contrast: a KNOWN universe (even empty) still previews the change in
        dry-run and makes no writes."""
        executor = self._make_executor(all_profile_ids=[1, 2, 3])
        exec_ctx = ExecutionContext(dry_run=True)
        exec_ctx.current_channel_id = 99
        action = {"type": "assign_channel_profile", "channel_profile_ids": [1]}

        result = self._run(executor, action, exec_ctx)

        assert result.success is True
        assert "EXCLUSIVE" in result.description
        assert "REMOVE from all" in result.description
        self.client.update_profile_channel.assert_not_called()

    def test_total_write_failure_is_not_modified(self):
        """y3m6o.1 Finding 3 (0152): when EVERY enable/disable PATCH raises, the
        channel changed nothing — the result must be success=False AND
        modified=False so add_result records no update count / rollback entity
        for a no-op."""
        self.client.update_profile_channel = AsyncMock(
            side_effect=RuntimeError("all writes fail")
        )
        executor = self._make_executor(all_profile_ids=[1, 2, 3])
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 99
        action = {"type": "assign_channel_profile", "channel_profile_ids": [1]}

        result = self._run(executor, action, exec_ctx)

        assert result.success is False
        assert result.modified is False
        # add_result recorded no channel update and no modified/rollback entity.
        assert exec_ctx.channels_updated == 0
        assert exec_ctx.modified_entities == []

    def test_partial_write_failure_is_still_modified(self):
        """y3m6o.1 Finding 3 (0152): a PARTIAL success (some PATCHes landed)
        stays modified=True so the real writes are counted and reversible, even
        though the action reports non-success."""
        def side_effect(pid, channel_id, body):
            if pid == 2:
                raise RuntimeError("one write fails")
            return None
        self.client.update_profile_channel = AsyncMock(side_effect=side_effect)
        executor = self._make_executor(all_profile_ids=[1, 2, 3])
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 99
        action = {"type": "assign_channel_profile", "channel_profile_ids": [1]}

        result = self._run(executor, action, exec_ctx)

        assert result.success is False
        assert result.modified is True
        # The successful enable(1) + disable(3) DID land, so the channel counts
        # as updated. It is NOT recorded as a rollback entity (Finding 6 —
        # profile membership is non-rollbackable).
        assert exec_ctx.channels_updated == 1
        assert exec_ctx.modified_entities == []

    def test_profile_assignment_is_not_recorded_for_rollback(self):
        """y3m6o.1 Finding 6 (0152): a SUCCESSFUL assign_channel_profile is
        counted as a channel update but records NO rollback entity — profile
        membership carries no reversible previous_state, so neither the legacy
        rollback nor the snapshot restore could reverse it. Marking it
        non-rollbackable keeps rollback metadata honest."""
        executor = self._make_executor(all_profile_ids=[1, 2, 3])
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 99
        action = {"type": "assign_channel_profile", "channel_profile_ids": [1]}

        result = self._run(executor, action, exec_ctx)

        assert result.success is True
        assert result.modified is True
        assert result.rollbackable is False
        assert exec_ctx.channels_updated == 1
        assert exec_ctx.modified_entities == []  # no misleading rollback entity

    def test_apply_channel_profile_to_channels_reconciles_each(self):
        """y3m6o.1 Finding 4 (0152): the event_sync entry point applies exclusive
        membership to an explicit set of channel ids, reusing the standard
        per-channel logic. Each channel: enable selected, disable the rest."""
        executor = self._make_executor(all_profile_ids=[1, 2, 3])
        exec_ctx = ExecutionContext()
        action = {"type": "assign_channel_profile", "channel_profile_ids": [1]}

        results = asyncio.get_event_loop().run_until_complete(
            executor.apply_channel_profile_to_channels(action, [50, 51], exec_ctx)
        )

        assert [r.success for r in results] == [True, True]
        # Both channels reconciled: 1 enabled, 2+3 disabled, per channel.
        per_channel = {}
        for c in self.client.update_profile_channel.call_args_list:
            pid, channel_id, body = c.args[0], c.args[1], c.args[2]
            per_channel.setdefault(channel_id, {})[pid] = body["enabled"]
        assert per_channel == {
            50: {1: True, 2: False, 3: False},
            51: {1: True, 2: False, 3: False},
        }
        # current_channel_id is restored after the loop (no leakage).
        assert exec_ctx.current_channel_id is None
        # Each successful reconcile counted through the shared add_result path.
        assert exec_ctx.channels_updated == 2

    def test_apply_channel_profile_to_channels_dedupes(self):
        """A channel touched by several attaches is reconciled ONCE."""
        executor = self._make_executor(all_profile_ids=[1, 2])
        exec_ctx = ExecutionContext()
        action = {"type": "assign_channel_profile", "channel_profile_ids": [1]}

        results = asyncio.get_event_loop().run_until_complete(
            executor.apply_channel_profile_to_channels(
                action, [50, 50, 50], exec_ctx
            )
        )

        assert len(results) == 1
        touched = {c.args[1] for c in self.client.update_profile_channel.call_args_list}
        assert touched == {50}


class TestProfileMembershipDiffOnlyWrites:
    """y3m6o.1 review follow-up: assign_channel_profile PATCHes ONLY the profiles
    whose enabled-state actually flips, given the run-start membership snapshot,
    so an idempotent reconcile makes zero writes and is not counted as a channel
    update (no channels_updated inflation)."""

    def setup_method(self):
        self.client = MagicMock()
        self.client.update_profile_channel = AsyncMock()
        self.stream_ctx = StreamContext(
            stream_id=1, stream_name="ESPN", m3u_account_id=1, m3u_account_name="P",
        )

    def _executor(self, *, universe, membership):
        """Executor with an EXPLICIT membership map so the diff engages. Every
        channel id in ``membership`` is treated as EXISTING at run start."""
        existing = [{"id": cid, "name": f"ch-{cid}"} for cid in membership]
        return ActionExecutor(
            self.client, existing_channels=existing,
            all_profile_ids=universe, channel_profile_membership=membership,
        )

    def _assign(self, executor, channel_id, selected, dry_run=False):
        exec_ctx = ExecutionContext(dry_run=dry_run)
        exec_ctx.current_channel_id = channel_id
        action = {"type": "assign_channel_profile",
                  "channel_profile_ids": list(selected)}
        result = asyncio.get_event_loop().run_until_complete(
            executor.execute(action, self.stream_ctx, exec_ctx)
        )
        return result, exec_ctx

    def _calls(self):
        return {c.args[0]: c.args[2]["enabled"]
                for c in self.client.update_profile_channel.call_args_list}

    def test_idempotent_reconcile_writes_nothing(self):
        """Channel already in EXACTLY the selected profiles -> ZERO PATCH calls,
        modified=False, channels_updated NOT incremented, success (green)."""
        executor = self._executor(universe=[1, 2, 3], membership={99: {1}})
        result, exec_ctx = self._assign(executor, 99, selected=(1,))

        assert result.success is True
        assert result.modified is False
        self.client.update_profile_channel.assert_not_called()
        assert exec_ctx.channels_updated == 0
        assert exec_ctx.modified_entities == []
        # No non-reversible flag either — nothing actually changed.
        assert exec_ctx.non_reversible_channel_ids == set()

    def test_real_change_patches_only_flips(self):
        """Only the profiles whose state flips are PATCHed. Channel in {1,2,3},
        select {1} -> disable 2,3 only (1 already enabled)."""
        executor = self._executor(universe=[1, 2, 3], membership={99: {1, 2, 3}})
        result, exec_ctx = self._assign(executor, 99, selected=(1,))

        assert result.success is True
        assert result.modified is True
        assert self._calls() == {2: False, 3: False}  # NOT {1: True, ...}
        assert exec_ctx.channels_updated == 1

    def test_real_change_enable_and_disable_flips(self):
        """Channel in {2,3}, select {1} -> enable 1 (flip) AND disable 2,3 (flips)."""
        executor = self._executor(universe=[1, 2, 3], membership={99: {2, 3}})
        result, _ = self._assign(executor, 99, selected=(1,))

        assert result.success is True
        assert self._calls() == {1: True, 2: False, 3: False}

    def test_failure_on_needed_flip_still_non_success(self):
        """A failed PATCH on a NEEDED flip still surfaces the failed id +
        non-success (truthful-failure preserved)."""
        def _fail_2(pid, cid, body):
            if pid == 2:
                raise RuntimeError("disable 2 failed")
            return None
        self.client.update_profile_channel = AsyncMock(side_effect=_fail_2)
        executor = self._executor(universe=[1, 2, 3], membership={99: {1, 2, 3}})
        result, _ = self._assign(executor, 99, selected=(1,))

        assert result.success is False
        assert "2" in (result.error or "")

    def test_no_op_profile_cannot_fail(self):
        """A profile that does not need changing is never PATCHed, so it can
        never fail: an idempotent reconcile stays green even if the client would
        raise on any write."""
        self.client.update_profile_channel = AsyncMock(
            side_effect=RuntimeError("must not be called")
        )
        executor = self._executor(universe=[1, 2, 3], membership={99: {1}})
        result, _ = self._assign(executor, 99, selected=(1,))

        assert result.success is True
        assert result.modified is False
        self.client.update_profile_channel.assert_not_called()

    def test_second_reconcile_same_run_is_noop(self):
        """After a reconcile, a SECOND reconcile of the same channel this run
        diffs against the freshly-updated membership -> zero writes."""
        executor = self._executor(universe=[1, 2, 3], membership={99: {1, 2, 3}})
        self._assign(executor, 99, selected=(1,))
        first = self.client.update_profile_channel.call_count
        assert first == 2  # disabled 2, 3

        self.client.update_profile_channel.reset_mock()
        result, exec_ctx = self._assign(executor, 99, selected=(1,))
        assert result.modified is False
        self.client.update_profile_channel.assert_not_called()

    def test_created_this_run_channel_disables_auto_joined_profiles(self):
        """A channel NOT in the run-start snapshot is treated as created this run
        -> Dispatcharr auto-joined it to ALL profiles -> select {1} disables the
        rest (verified live auto-join behavior)."""
        executor = self._executor(universe=[1, 2, 3], membership={})  # empty map
        result, _ = self._assign(executor, 500, selected=(1,))

        assert result.success is True
        assert self._calls() == {2: False, 3: False}

    def test_idempotent_dry_run_reports_no_change(self):
        """Dry-run of an already-correct channel reports modified=False and no
        writes (honest preview)."""
        executor = self._executor(universe=[1, 2, 3], membership={99: {1}})
        result, _ = self._assign(executor, 99, selected=(1,), dry_run=True)

        assert result.success is True
        assert result.modified is False
        # y3m6o.2: even a no-change preview states the exclusive/subtractive
        # contract (would still enforce removal from all other profiles).
        assert "no change" in result.description.lower()
        assert "EXCLUSIVE" in result.description
        assert "removal from all" in result.description.lower()
        self.client.update_profile_channel.assert_not_called()

    def test_dry_run_states_both_halves_and_makes_no_writes(self):
        """y3m6o.2 (acceptance criterion 1): a CHANGE preview names BOTH the
        selected/enabled impact AND the subtractive removal from every OTHER
        profile, computed WITHOUT any mutating client call. Channel 99 is a
        member of {1, 2}; selecting {1} disables 2 and (subtractively) removes
        it from profile 3 as well — the complement the operator must be told
        about."""
        executor = self._executor(universe=[1, 2, 3], membership={99: {1, 2}})
        result, _ = self._assign(executor, 99, selected=(1,), dry_run=True)

        assert result.success is True
        assert result.modified is True
        # Both halves stated: enable in the selected set AND remove from all
        # other channel profiles (the destructive complement).
        assert "EXCLUSIVE" in result.description
        assert "[1]" in result.description  # names the selected set
        assert "REMOVE from all 2 other channel profile(s)" in result.description
        # No-mutation guarantee: the dry-run touched no write method on the
        # Dispatcharr client.
        self.client.update_profile_channel.assert_not_called()
        assert self._calls() == {}

    def test_event_sync_path_diffs_and_noops_when_correct(self):
        """The event_sync entry point (apply_channel_profile_to_channels) inherits
        the diff: a channel already correctly reconciled makes zero writes."""
        executor = self._executor(
            universe=[1, 2, 3], membership={50: {1}, 51: {1, 2, 3}},
        )
        exec_ctx = ExecutionContext()
        action = {"type": "assign_channel_profile", "channel_profile_ids": [1]}
        results = asyncio.get_event_loop().run_until_complete(
            executor.apply_channel_profile_to_channels(action, [50, 51], exec_ctx)
        )

        assert [r.success for r in results] == [True, True]
        # Channel 50 already correct (no writes); channel 51 disables 2, 3.
        per_channel = {}
        for c in self.client.update_profile_channel.call_args_list:
            per_channel.setdefault(c.args[1], {})[c.args[0]] = c.args[2]["enabled"]
        assert per_channel == {51: {2: False, 3: False}}
        # Only channel 51 counted as updated (50 was a no-op).
        assert exec_ctx.channels_updated == 1
        assert results[0].modified is False  # channel 50
        assert results[1].modified is True   # channel 51


class TestAssignChannelProfileProvenanceMarker:
    """GH #720 Part B (decision 2b): assign_channel_profile stamps a durable
    provenance marker into the channel's Dispatcharr custom_properties so the
    group-level profile reconcile excludes it (pipeline action > group
    selection). These tests pin that the marker is written, merged over
    existing custom_properties, and never written in a dry run."""

    def setup_method(self):
        self.client = MagicMock()
        self.client.update_profile_channel = AsyncMock()
        self.client.update_channel = AsyncMock()
        # get_channel returns a non-dict by default so the marker helper's
        # fresh-fetch cleanly falls back to the run cache (Blocker 2); tests that
        # exercise a concurrent write override its return_value.
        self.client.get_channel = AsyncMock(return_value=None)
        self.stream_ctx = StreamContext(
            stream_id=201,
            stream_name="ESPN2 HD",
            m3u_account_id=1,
            m3u_account_name="Provider A",
            group_name="Sports",
        )

    def _run(self, executor, action, exec_ctx, rule_id=None):
        return asyncio.get_event_loop().run_until_complete(
            executor.execute(action, self.stream_ctx, exec_ctx, rule_id=rule_id)
        )

    def _marker(self):
        from services.profile_reconcile import (
            PIPELINE_OWNERSHIP_MARKER_KEY,
            PIPELINE_OWNERSHIP_MARKER_VALUE,
        )
        return PIPELINE_OWNERSHIP_MARKER_KEY, PIPELINE_OWNERSHIP_MARKER_VALUE

    def _rule_id_key(self):
        from services.profile_reconcile import PIPELINE_OWNERSHIP_RULE_ID_KEY
        return PIPELINE_OWNERSHIP_RULE_ID_KEY

    def test_marker_written_on_assign(self):
        executor = ActionExecutor(self.client, all_profile_ids=[1, 2, 3])
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 99
        action = {"type": "assign_channel_profile", "channel_profile_ids": [1]}

        result = self._run(executor, action, exec_ctx)

        assert result.success is True
        self.client.update_channel.assert_called_once()
        cid, body = self.client.update_channel.call_args.args
        assert cid == 99
        key, value = self._marker()
        assert body["custom_properties"][key] == value

    def test_marker_merges_over_existing_custom_properties(self):
        existing = [
            {"id": 99, "name": "ESPN2 HD", "custom_properties": {"custom_epg_id": 7}}
        ]
        # The marker helper now merges over the FRESH-fetched custom_properties
        # (Blocker 2/5), so the authoritative current state is what get_channel
        # returns.
        self.client.get_channel = AsyncMock(
            return_value={"id": 99, "custom_properties": {"custom_epg_id": 7}}
        )
        executor = ActionExecutor(
            self.client, existing_channels=existing, all_profile_ids=[1, 2]
        )
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 99
        action = {"type": "assign_channel_profile", "channel_profile_ids": [1]}

        self._run(executor, action, exec_ctx)

        _cid, body = self.client.update_channel.call_args.args
        key, value = self._marker()
        # Pre-existing key preserved, marker added.
        assert body["custom_properties"]["custom_epg_id"] == 7
        assert body["custom_properties"][key] == value

    def test_marker_skipped_when_already_marked(self):
        """Idempotent: a channel already carrying the ownership marker must NOT
        trigger a redundant update_channel PATCH (decision 2b — the stamp is
        write-once)."""
        key, value = self._marker()
        existing = [
            {
                "id": 99,
                "name": "ESPN2 HD",
                "custom_properties": {key: value, "custom_epg_id": 7},
            }
        ]
        executor = ActionExecutor(
            self.client, existing_channels=existing, all_profile_ids=[1, 2]
        )
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 99
        action = {"type": "assign_channel_profile", "channel_profile_ids": [1]}

        result = self._run(executor, action, exec_ctx)

        assert result.success is True
        # Already marked — no marker write is issued.
        self.client.update_channel.assert_not_called()

    def test_no_marker_write_in_dry_run(self):
        executor = ActionExecutor(self.client, all_profile_ids=[1, 2])
        exec_ctx = ExecutionContext(dry_run=True)
        exec_ctx.current_channel_id = 99
        action = {"type": "assign_channel_profile", "channel_profile_ids": [1]}

        self._run(executor, action, exec_ctx)

        self.client.update_channel.assert_not_called()

    def test_marker_write_failure_does_not_fail_assignment(self):
        self.client.update_channel = AsyncMock(side_effect=RuntimeError("boom"))
        executor = ActionExecutor(self.client, all_profile_ids=[1, 2])
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 99
        action = {"type": "assign_channel_profile", "channel_profile_ids": [1]}

        result = self._run(executor, action, exec_ctx)

        # Profiles were still applied; the marker failure is swallowed.
        assert result.success is True

    def test_marker_write_failure_surfaces_nonfatal_partial(self):
        """Blocker 2: a marker-write failure keeps success=True (profiles ARE
        applied) but is surfaced non-fatally — result.error is set and the
        description notes precedence was not established, so the run reflects the
        incompleteness instead of a silent clean success."""
        self.client.update_channel = AsyncMock(side_effect=RuntimeError("boom"))
        executor = ActionExecutor(self.client, all_profile_ids=[1, 2])
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 99
        action = {"type": "assign_channel_profile", "channel_profile_ids": [1]}

        result = self._run(executor, action, exec_ctx, rule_id=5)

        assert result.success is True
        assert result.error and "precedence" in result.error
        assert "precedence not established" in result.description

    def test_marker_write_skipped_on_failed_fresh_read(self):
        """Blocker 5: a FAILED fresh read must SKIP the marker write entirely (no
        PATCH from stale cache) and record ownership-unestablished; the profile
        assignment itself still succeeds."""
        self.client.get_channel = AsyncMock(side_effect=RuntimeError("read boom"))
        executor = ActionExecutor(self.client, all_profile_ids=[1, 2])
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 99
        action = {"type": "assign_channel_profile", "channel_profile_ids": [1]}

        result = self._run(executor, action, exec_ctx, rule_id=5)

        assert result.success is True
        self.client.update_channel.assert_not_called()  # no stale write
        assert 99 in exec_ctx.profile_ownership_unestablished_channel_ids

    def test_marker_add_preserves_concurrent_custom_properties(self):
        """Blocker 2 (clobber, add direction): a concurrent custom_properties
        write landing between the run snapshot and the marker PATCH is preserved
        because the helper fresh-fetches current custom_properties right before
        the merge."""
        # Run cache has NO concurrent key; the FRESH fetch returns it.
        self.client.get_channel = AsyncMock(
            return_value={"id": 99, "custom_properties": {"custom_epg_id": 77}}
        )
        executor = ActionExecutor(self.client, all_profile_ids=[1, 2])
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 99
        action = {"type": "assign_channel_profile", "channel_profile_ids": [1]}

        self._run(executor, action, exec_ctx, rule_id=5)

        _cid, body = self.client.update_channel.call_args.args
        key, value = self._marker()
        cp = body["custom_properties"]
        assert cp["custom_epg_id"] == 77   # concurrent write preserved
        assert cp[key] == value
        assert cp[self._rule_id_key()] == 5

    def test_marker_stamps_owning_rule_id(self):
        """Blocker 2 handoff: the owning rule id is stamped alongside the owner
        marker so the reconcile can release the channel when the rule is gone."""
        executor = ActionExecutor(self.client, all_profile_ids=[1, 2])
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 99
        action = {"type": "assign_channel_profile", "channel_profile_ids": [1]}

        self._run(executor, action, exec_ctx, rule_id=42)

        _cid, body = self.client.update_channel.call_args.args
        key, value = self._marker()
        assert body["custom_properties"][key] == value
        assert body["custom_properties"][self._rule_id_key()] == 42

    def test_marker_rewritten_when_owning_rule_id_changes(self):
        """A channel already owned by rule 1 that is re-assigned by rule 2 gets
        re-stamped (idempotency keys on owner AND rule id, not owner alone)."""
        key, value = self._marker()
        rid_key = self._rule_id_key()
        existing = [
            {"id": 99, "name": "ESPN2 HD",
             "custom_properties": {key: value, rid_key: 1}}
        ]
        executor = ActionExecutor(
            self.client, existing_channels=existing, all_profile_ids=[1, 2]
        )
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 99
        action = {"type": "assign_channel_profile", "channel_profile_ids": [1]}

        self._run(executor, action, exec_ctx, rule_id=2)

        # Re-stamped because the owning rule changed 1 -> 2.
        self.client.update_channel.assert_called_once()
        _cid, body = self.client.update_channel.call_args.args
        assert body["custom_properties"][rid_key] == 2

    def test_marker_idempotent_when_same_rule_id_skips_without_a_get(self):
        """Same owner AND same rule id already present -> no redundant write AND
        (Should-Fix 4) NO fresh-fetch GET: the idempotent skip is decided on the
        run cache, so a rule re-run over marked channels issues zero extra GETs."""
        key, value = self._marker()
        rid_key = self._rule_id_key()
        existing = [
            {"id": 99, "name": "ESPN2 HD",
             "custom_properties": {key: value, rid_key: 5}}
        ]
        executor = ActionExecutor(
            self.client, existing_channels=existing, all_profile_ids=[1, 2]
        )
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 99
        action = {"type": "assign_channel_profile", "channel_profile_ids": [1]}

        self._run(executor, action, exec_ctx, rule_id=5)

        self.client.update_channel.assert_not_called()
        self.client.get_channel.assert_not_called()  # fast-path, no GET

    def test_marker_write_failure_records_run_warning_signal(self):
        """Judgment 4b: a marker-write failure records the channel on
        exec_ctx.profile_ownership_unestablished_channel_ids (the engine folds it
        into a run-level WARNING) while the assignment stays success=True."""
        self.client.update_channel = AsyncMock(side_effect=RuntimeError("boom"))
        executor = ActionExecutor(self.client, all_profile_ids=[1, 2])
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 99
        action = {"type": "assign_channel_profile", "channel_profile_ids": [1]}

        result = self._run(executor, action, exec_ctx, rule_id=5)

        assert result.success is True
        assert 99 in exec_ctx.profile_ownership_unestablished_channel_ids


class TestAssignDefaultProfiles:
    """Guards the _assign_default_profiles refactor (shared helper) — still
    enables the configured defaults and disables every other known profile."""

    def test_enables_defaults_disables_the_rest(self):
        client = MagicMock()
        client.update_profile_channel = AsyncMock()
        settings = MagicMock()
        settings.default_channel_profile_ids = [1, 3]
        executor = ActionExecutor(client, settings=settings, all_profile_ids=[1, 2, 3])

        desc = asyncio.get_event_loop().run_until_complete(
            executor._assign_default_profiles(99)
        )

        calls = {}
        for call in client.update_profile_channel.call_args_list:
            calls[call.args[0]] = call.args[2]["enabled"]
        assert calls == {1: True, 2: False, 3: True}
        assert "enabled in 2" in desc
        assert "disabled in 1" in desc

    def test_no_defaults_configured_returns_empty_no_calls(self):
        client = MagicMock()
        client.update_profile_channel = AsyncMock()
        settings = MagicMock()
        settings.default_channel_profile_ids = []
        executor = ActionExecutor(client, settings=settings, all_profile_ids=[1, 2, 3])

        desc = asyncio.get_event_loop().run_until_complete(
            executor._assign_default_profiles(99)
        )

        assert desc == ""
        client.update_profile_channel.assert_not_called()

    def test_unavailable_universe_is_benign_noop(self):
        """Default-profile assignment is a best-effort enhancement, NOT the
        user's explicit rule action: an unavailable universe (None) must stay a
        benign no-op here (return ""), never a hard failure — unlike the
        explicit assign_channel_profile action, which fails on None."""
        client = MagicMock()
        client.update_profile_channel = AsyncMock()
        settings = MagicMock()
        settings.default_channel_profile_ids = [1, 3]
        executor = ActionExecutor(client, settings=settings, all_profile_ids=None)

        desc = asyncio.get_event_loop().run_until_complete(
            executor._assign_default_profiles(99)
        )

        assert desc == ""
        client.update_profile_channel.assert_not_called()

    def test_genuinely_empty_universe_is_benign_noop(self):
        """A genuinely-empty universe ([]) is likewise a benign no-op for
        default-profile assignment."""
        client = MagicMock()
        client.update_profile_channel = AsyncMock()
        settings = MagicMock()
        settings.default_channel_profile_ids = [1, 3]
        executor = ActionExecutor(client, settings=settings, all_profile_ids=[])

        desc = asyncio.get_event_loop().run_until_complete(
            executor._assign_default_profiles(99)
        )

        assert desc == ""
        client.update_profile_channel.assert_not_called()

    def test_partial_failure_logs_failed_ids_but_stays_best_effort(self, caplog):
        """y3m6o.1 Finding 5 (0152): default-profile assignment stays best-effort
        (never aborts channel creation), but a partial/total PATCH failure is no
        longer SILENT — it emits a structured [AUTO-CREATE-EXEC] warning naming
        the failed profile id(s). The return description is unchanged."""
        import logging

        def side_effect(pid, channel_id, body):
            if pid == 2:  # a disable that fails
                raise RuntimeError("boom")
            return None
        client = MagicMock()
        client.update_profile_channel = AsyncMock(side_effect=side_effect)
        settings = MagicMock()
        settings.default_channel_profile_ids = [1, 3]
        executor = ActionExecutor(client, settings=settings, all_profile_ids=[1, 2, 3])

        with caplog.at_level(logging.WARNING):
            desc = asyncio.get_event_loop().run_until_complete(
                executor._assign_default_profiles(99)
            )

        # Best-effort: the successful writes still land and produce a desc.
        assert "enabled in 2" in desc  # profiles 1 and 3 enabled
        # The failed profile id (2) is surfaced in a structured warning.
        warning_text = "\n".join(
            r.message for r in caplog.records if r.levelno == logging.WARNING
        )
        assert "default-profile assignment incomplete" in warning_text
        assert "2" in warning_text

    def test_full_success_emits_no_failure_warning(self, caplog):
        """No failed ids => no incomplete-assignment warning (purely additive)."""
        import logging

        client = MagicMock()
        client.update_profile_channel = AsyncMock()
        settings = MagicMock()
        settings.default_channel_profile_ids = [1, 3]
        executor = ActionExecutor(client, settings=settings, all_profile_ids=[1, 2, 3])

        with caplog.at_level(logging.WARNING):
            asyncio.get_event_loop().run_until_complete(
                executor._assign_default_profiles(99)
            )

        warning_text = "\n".join(
            r.message for r in caplog.records if r.levelno == logging.WARNING
        )
        assert "incomplete" not in warning_text


class TestChannelNumberTenths:
    """A pipeline rule may name a tenth, and the engine has to honour it.

    Bead ``enhancedchannelmanager-ay3iq``. ``1.1`` became a valid channel number
    everywhere under bead ``enhancedchannelmanager-ic884.1``, but the executor
    understood only whole numbers, so a rule set to ``1.1`` was accepted and
    then quietly assigned an unrelated integer. These tests pin the fractional
    result itself: never an integer substitution, never a silent fallback to
    automatic numbering.
    """

    def _executor(self, numbers=(), settings=None):
        client = MagicMock()
        client.create_channel = AsyncMock()
        client.update_channel = AsyncMock()
        channels = [
            {"id": index, "name": f"CH{index}", "channel_number": number,
             "streams": [], "auto_created": True}
            for index, number in enumerate(numbers, start=1)
        ]
        return ActionExecutor(client, existing_channels=channels, settings=settings)

    def _stream_ctx(self):
        return StreamContext(
            stream_id=201, stream_name="ESPN HD", m3u_account_id=1,
            m3u_account_name="Provider A", group_name="Sports",
        )

    def test_a_fractional_spec_keeps_its_own_number(self):
        """The defect: this used to answer 3, the next free INTEGER."""
        executor = self._executor([1, 2])
        assert executor._get_next_channel_number(1.1) == 1.1

    def test_a_fractional_string_spec_keeps_its_own_number(self):
        executor = self._executor([1, 2])
        assert executor._get_next_channel_number("1.1") == 1.1

    def test_a_taken_tenth_moves_to_the_next_tenth_not_the_next_integer(self):
        """The bead's collision case: 1.1 is taken, so the answer is 1.2, not 2."""
        executor = self._executor([1, 1.1, 2])
        assert executor._get_next_channel_number(1.1) == 1.2

    def test_the_walk_carries_no_float_dust(self):
        """``0.7 + 0.1`` is ``0.7999999999999999``, which is not a channel number."""
        executor = self._executor([0.7])
        assigned = executor._get_next_channel_number(0.7)
        assert assigned == 0.8
        assert is_valid_channel_number(assigned)

    def test_a_full_tenths_run_carries_over_into_the_next_whole_number(self):
        executor = self._executor([1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9])
        assigned = executor._get_next_channel_number(1.1)
        assert assigned == 2
        assert is_valid_channel_number(assigned)

    def test_every_number_the_walk_hands_out_is_in_contract(self):
        """Ten consecutive assignments from a fractional start, all on the grid."""
        executor = self._executor()
        assigned = []
        for _ in range(10):
            number = executor._get_next_channel_number(20.1)
            executor._mark_channel_number_used(number)
            assigned.append(number)
        assert assigned == [20.1, 20.2, 20.3, 20.4, 20.5, 20.6, 20.7, 20.8, 20.9, 21]
        assert all(is_valid_channel_number(number) for number in assigned)

    def test_a_whole_spec_still_steps_by_one(self):
        """Unchanged behaviour: only a spec that names a tenth walks by a tenth."""
        executor = self._executor([100, 101])
        assigned = executor._get_next_channel_number(100)
        assert assigned == 102
        assert isinstance(assigned, int)

    def test_a_whole_float_spec_answers_a_whole_number(self):
        """``800.0`` must not reach a payload or a channel name as ``800.0``."""
        executor = self._executor()
        assigned = executor._get_next_channel_number(800.0)
        assert assigned == 800
        assert isinstance(assigned, int)

    def test_a_fractional_range_is_not_honoured_and_says_so(self, caplog):
        """Ranges stay whole-number, so a stored fractional range must not go quiet."""
        import logging

        executor = self._executor([1, 2])
        with caplog.at_level(logging.WARNING):
            assigned = executor._get_next_channel_number("1.1-1.9")
        assert assigned == 3  # Automatic numbering, as before.
        assert "1.1-1.9" in caplog.text

    def test_a_template_spec_stays_quiet(self, caplog):
        """``{auto}`` is documented rule vocabulary, so it must not warn."""
        import logging

        executor = self._executor([1, 2])
        with caplog.at_level(logging.WARNING):
            assert executor._get_next_channel_number("{auto}") == 3
        assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []

    def test_a_fractional_number_reaches_dispatcharr_unchanged(self):
        """The seam that matters: what the pipeline actually POSTs."""
        executor = self._executor([1, 2])
        executor.client.create_channel.return_value = {
            "id": 9, "name": "ESPN HD", "channel_number": 1.1,
        }
        action = {
            "type": "create_channel",
            "name_template": "{stream_name}",
            "channel_number": 1.1,
        }
        result = asyncio.get_event_loop().run_until_complete(
            executor.execute(action, self._stream_ctx(), ExecutionContext())
        )
        assert result.success is True
        posted = executor.client.create_channel.call_args[0][0]
        assert posted["channel_number"] == 1.1

    def test_set_channel_number_writes_the_tenth_it_was_given(self):
        executor = self._executor([1, 2])
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 1
        result = asyncio.get_event_loop().run_until_complete(
            executor.execute(
                {"type": "set_channel_number", "value": "1.1"},
                self._stream_ctx(),
                exec_ctx,
            )
        )
        assert result.success is True
        executor.client.update_channel.assert_awaited_once_with(
            1, {"channel_number": 1.1}
        )

    def test_a_dry_run_previews_the_tenth(self):
        executor = self._executor([1, 2])
        result = asyncio.get_event_loop().run_until_complete(
            executor.execute(
                {"type": "create_channel", "name_template": "{stream_name}",
                 "channel_number": 1.1},
                self._stream_ctx(),
                ExecutionContext(dry_run=True),
            )
        )
        assert result.success is True
        assert "#1.1" in result.description
        executor.client.create_channel.assert_not_called()

    def test_the_name_prefix_renders_a_tenth_as_a_tenth(self):
        """``include_channel_number_in_name`` must print 1.1, and 800 as 800."""
        executor = self._executor(settings=SimpleNamespace(
            include_channel_number_in_name=True, channel_number_separator="-"
        ))
        assert executor._apply_channel_number_in_name("ESPN", 1.1) == "1.1 - ESPN"
        assert executor._apply_channel_number_in_name("ESPN", 800) == "800 - ESPN"


class TestChannelNumberOccupancy:
    """A number an existing channel already holds is never handed out again.

    Two surviving instances of the "silent wrong value" class the epic exists
    to close, both found by external adversarial review of branch
    ``fix/epic-yajww-operator-followups``:

    * Occupancy was seeded with a truthiness test, so a channel numbered ``0``
      was read as free and the engine created a second one.
    * The tenths walk trusted a tick to name its own float. At or above
      ``2**49`` adjacent floats are 0.125 apart, wider than a tenth, so the
      walk divided the next tick back to the SAME float it had just rejected
      as taken and returned it.
    """

    def _executor(self, numbers=()):
        client = MagicMock()
        client.create_channel = AsyncMock()
        client.update_channel = AsyncMock()
        channels = [
            {"id": index, "name": f"CH{index}", "channel_number": number,
             "streams": [], "auto_created": True}
            for index, number in enumerate(numbers, start=1)
        ]
        return ActionExecutor(client, existing_channels=channels)

    # -- Channel 0 --------------------------------------------------------

    def test_an_occupied_channel_zero_is_recorded_as_taken(self):
        """``0`` is falsy but in contract, so presence decides, not truthiness."""
        executor = self._executor([0])
        assert executor._used_channel_number_ticks == {0}
        assert executor._is_channel_number_used(0) is True

    def test_a_rule_naming_zero_does_not_duplicate_an_occupied_zero(self):
        """The defect: this handed back ``0``, creating a second channel 0."""
        executor = self._executor([0])
        assert executor._get_next_channel_number(0) == 1

    def test_a_rule_naming_zero_gets_zero_when_zero_is_free(self):
        """The other half of the contract: 0 is assignable, not reserved."""
        executor = self._executor([1, 2])
        assert executor._get_next_channel_number(0) == 0

    def test_an_unassigned_channel_leaves_zero_free(self):
        """``None`` is Dispatcharr's normal "no number" state, not channel 0."""
        executor = self._executor([None])
        assert executor._used_channel_number_ticks == set()
        assert executor._get_next_channel_number(0) == 0

    @pytest.mark.parametrize("stored", ["", "not a number", float("nan"), float("inf")])
    def test_a_stored_value_that_is_no_channel_number_does_not_break_startup(self, stored):
        """Dispatcharr enforces nothing on this column, so construction must survive it."""
        executor = self._executor([stored, 0])
        assert executor._used_channel_number_ticks == {0}
        assert executor._get_next_channel_number(0) == 1

    def test_a_zero_tenth_start_walks_off_an_occupied_zero(self):
        """``0.0`` names the same slot as ``0``, and steps by a whole number."""
        executor = self._executor([0, 1])
        assert executor._get_next_channel_number(0.0) == 2

    # -- The float bound --------------------------------------------------

    def test_a_large_fractional_start_never_answers_the_occupied_number(self):
        """The reproduction: ``2**50 + 0.5`` used to come straight back."""
        start = 1125899906842624.5
        executor = self._executor([start])
        assigned = executor._get_next_channel_number(start)
        assert assigned != start
        assert executor._is_channel_number_used(assigned) is False
        assert assigned == 1125899906842625
        assert is_valid_channel_number(assigned)

    def test_the_collapsed_tenth_is_audible_not_silent(self, caplog):
        """A different answer than the rule asked for has to say so."""
        import logging

        start = 1125899906842624.5
        executor = self._executor([start])
        with caplog.at_level(logging.WARNING):
            assigned = executor._get_next_channel_number(start)
        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("not representable at this magnitude" in text for text in warnings), warnings
        assert any(str(assigned) in text for text in warnings), warnings

    def test_an_ordinary_tenth_stays_quiet(self, caplog):
        """The collapse warning must not fire at magnitudes floats handle."""
        import logging

        executor = self._executor([1.1])
        with caplog.at_level(logging.WARNING):
            assert executor._get_next_channel_number(1.1) == 1.2
        assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []

    @pytest.mark.parametrize(
        "start",
        [
            562949953421312.5,   # 2**49 + 0.5, the first magnitude that collapses
            1125899906842624.5,  # 2**50 + 0.5
            2251799813685248.5,  # 2**51 + 0.5
            9007199254740992.0,  # 2**53, every float here is a whole number
            1e308,
        ],
    )
    def test_the_walk_terminates_and_answers_a_free_number_at_any_magnitude(self, start):
        executor = self._executor([start])
        assigned = executor._get_next_channel_number(start)
        assert executor._is_channel_number_used(assigned) is False
        assert is_valid_channel_number(assigned)

    def test_consecutive_assignments_at_the_bound_are_all_distinct(self):
        """Marking one used must actually move the next answer along."""
        start = 1125899906842624.5
        executor = self._executor([start])
        assigned = []
        for _ in range(5):
            number = executor._get_next_channel_number(start)
            executor._mark_channel_number_used(number)
            assigned.append(number)
        assert len(set(assigned)) == 5
        assert assigned == [
            1125899906842625,
            1125899906842626,
            1125899906842627,
            1125899906842628,
            1125899906842629,
        ]

    def test_a_whole_tick_that_will_not_round_trip_fails_instead_of_hanging(self):
        """The walk's own termination guard, exercised against its trigger.

        A whole tick divides back to a Python ``int`` and multiplies straight
        back, so this cannot happen for real. If it ever did, the walk would
        step to another whole tick and repeat forever, so the guard has to be
        the thing that fires. Broken here at the boundary the walk calls.
        """
        executor = self._executor()
        with patch("channel_pipeline_executor.channel_number_ticks", return_value=-1):
            with pytest.raises(RuntimeError, match="does not round-trip"):
                executor._get_next_channel_number("auto")

    def test_a_tenth_below_the_bound_is_still_honoured_as_a_tenth(self):
        """The whole-number fallback must not reach magnitudes floats can hold."""
        start = 281474976710656.5  # 2**48 + 0.5, the last magnitude with tenths
        executor = self._executor([start])
        assigned = executor._get_next_channel_number(start)
        assert assigned == 281474976710656.6
        assert executor._is_channel_number_used(assigned) is False

    # -- Ranges -----------------------------------------------------------

    def test_a_range_still_answers_the_first_free_number_inside_it(self):
        """Unchanged behaviour: a range is searched from its own bottom up."""
        executor = self._executor([100, 101])
        assigned = executor._get_next_channel_number("100-105")
        assert assigned == 102
        assert isinstance(assigned, int)

    def test_an_exhausted_range_does_not_hand_back_an_occupied_number(self):
        """The defect: ``"1-2"`` against 1, 2, 3 used to answer 3.

        Spilling past the top of an exhausted range is the intended behaviour.
        Spilling onto a number this engine recorded as taken at startup is the
        silent-wrong-value class the epic exists to close: two channels end up
        sharing a number, and nothing says so.
        """
        executor = self._executor([1, 2, 3])
        assigned = executor._get_next_channel_number("1-2")
        assert assigned == 4
        assert executor._is_channel_number_used(assigned) is False

    def test_an_exhausted_range_spills_past_a_whole_run_of_occupied_numbers(self):
        """One step past the top is not enough when the spill target is a run."""
        executor = self._executor([1, 2, 3, 4, 5])
        assigned = executor._get_next_channel_number("1-2")
        assert assigned == 6
        assert executor._is_channel_number_used(assigned) is False

    def test_an_exhausted_range_still_spills_past_the_top_when_the_top_is_free(self):
        """The behaviour the spill exists for, unchanged: never fail, go above."""
        executor = self._executor([10, 11, 12])
        assigned = executor._get_next_channel_number("10-12")
        assert assigned == 13
        assert is_valid_channel_number(assigned)

    def test_an_exhausted_range_answers_a_whole_number_not_a_tenth(self):
        """Ranges stay whole-number, so the spill must not walk onto a tenth."""
        executor = self._executor([1, 2, 3, 3.1, 3.2])
        assigned = executor._get_next_channel_number("1-2")
        assert assigned == 4
        assert isinstance(assigned, int)

    def test_consecutive_range_assignments_are_all_distinct(self):
        """Marking one used must move the next answer along, inside and above."""
        executor = self._executor([1])
        assigned = []
        for _ in range(4):
            number = executor._get_next_channel_number("1-2")
            executor._mark_channel_number_used(number)
            assigned.append(number)
        assert assigned == [2, 3, 4, 5]
        assert all(is_valid_channel_number(number) for number in assigned)
