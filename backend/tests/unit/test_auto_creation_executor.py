"""
Unit tests for the auto-creation executor service.

Tests the ActionExecutor class which executes actions against channels, groups,
and streams with proper rollback tracking.
"""
from unittest.mock import MagicMock, AsyncMock, patch
import asyncio

from auto_creation_executor import (
    ActionResult,
    ExecutionContext,
    ActionExecutor,
)
from auto_creation_evaluator import StreamContext


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
            {"id": 1, "name": "ESPN", "channel_number": 100},
            {"id": 2, "name": "CNN", "channel_number": 200},
        ]
        executor = ActionExecutor(client, existing_channels=channels)

        assert len(executor.existing_channels) == 2
        assert executor._channel_by_id[1]["name"] == "ESPN"
        assert executor._channel_by_name["espn"]["id"] == 1
        assert 100 in executor._used_channel_numbers
        assert 200 in executor._used_channel_numbers

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
            {"id": 1, "name": "ESPN", "tvg_id": "ESPN.US", "channel_number": 100},
            {"id": 2, "name": "ESPN2", "tvg_id": "ESPN2.US", "channel_number": 101},
            {"id": 3, "name": "CNN", "tvg_id": "CNN.US", "channel_number": 200},
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
        self.executor._created_channels["fox"] = {"id": 99, "name": "FOX"}
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
            {"id": 1, "name": "ESPN", "tvg_id": "ESPN.US", "channel_number": 100, "streams": [101]},
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
            {"id": 1, "name": "ESPN", "channel_number": 100, "streams": [101]},
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
        from auto_creation_executor import ActionExecutor

        client = MagicMock()
        client.update_channel = AsyncMock()
        client.create_channel = AsyncMock()

        # Existing channel already stored under the normalized name.
        existing = [{"id": 42, "name": "RTL", "streams": [], "channel_group_id": 5}]
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
        from auto_creation_executor import ActionExecutor

        client = MagicMock()
        client.update_channel = AsyncMock(return_value={})
        client.create_channel = AsyncMock()

        existing = [{"id": 7, "name": "107 | RTL ᴿᴬᵂ", "channel_number": 107,
                     "streams": [], "channel_group_id": 5}]
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
            {"id": 1, "name": "ESPN", "tvg_id": "ESPN.US", "channel_number": 100, "streams": [101]},
            {"id": 2, "name": "ESPN2", "tvg_id": "ESPN2.US", "channel_number": 101, "streams": []},
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
                     "channel_group_id": 9}]
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


class TestActionExecutorPropertyActions:
    """Tests for property assignment actions."""

    def setup_method(self):
        """Set up test fixtures."""
        self.client = MagicMock()
        self.client.update_channel = AsyncMock()
        self.client.create_logo = AsyncMock(return_value={"id": 42})
        self.client.find_logo_by_url = AsyncMock(return_value=None)

        self.channels = [
            {"id": 1, "name": "ESPN", "logo_url": None, "tvg_id": None},
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


class TestActionExecutorDryRun:
    """Tests for dry run mode across all actions."""

    def setup_method(self):
        """Set up test fixtures."""
        self.client = MagicMock()
        self.client.update_channel = AsyncMock()
        self.client.create_channel = AsyncMock()
        self.client.create_channel_group = AsyncMock()

        self.channels = [
            {"id": 1, "name": "ESPN", "channel_number": 100, "streams": [101]},
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
            {"id": 1, "name": "ESPN", "logo_url": None, "tvg_id": None},
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
        from auto_creation_schema import Action
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
             "channel_group_id": 1, "streams": [101]},
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
             "channel_group_id": 1, "streams": [101]},
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
    auto_creation_executor.py): after the target channel is resolved, the merge
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
             "channel_group_id": 1, "streams": [101]},
            {"id": 20, "name": "Excluded", "channel_number": 200,
             "channel_group_id": 567, "streams": [201]},
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
        from auto_creation_executor import ActionExecutor, ExecutionContext
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
        from auto_creation_executor import ActionExecutor, ExecutionContext
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
    """bd-0emgo.5: per-merge journal entry for lightweight recoverability.

    Each LIVE merge in ``_add_stream_to_channel`` must write one
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
             "channel_number": 100, "streams": [101]},
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
        return asyncio.get_event_loop().run_until_complete(
            executor.execute(action, self.stream_ctx, exec_ctx)
        )

    def test_live_merge_writes_one_journal_entry(self):
        """A live merge writes exactly one auto_creation/merge_stream entry."""
        with patch("auto_creation_executor.journal.log_entry") as mock_log:
            result = self._run_live_merge(execution_id=42)

        assert result.success is True
        assert result.modified is True
        mock_log.assert_called_once()
        kwargs = mock_log.call_args.kwargs
        assert kwargs["category"] == "auto_creation"
        assert kwargs["action_type"] == "merge_stream"
        assert kwargs["entity_id"] == 1  # channel_id
        assert kwargs["entity_name"] == "ESPN"
        assert kwargs["user_initiated"] is False

    def test_journal_batch_id_is_execution_id(self):
        """batch_id carries the run's execution_id (as a string)."""
        with patch("auto_creation_executor.journal.log_entry") as mock_log:
            self._run_live_merge(execution_id=42)

        kwargs = mock_log.call_args.kwargs
        assert kwargs["batch_id"] == "42"

    def test_journal_before_after_carry_stream_ids_only(self):
        """before/after hold stream IDs only — never URLs/objects (creds)."""
        with patch("auto_creation_executor.journal.log_entry") as mock_log:
            self._run_live_merge(execution_id=42)

        kwargs = mock_log.call_args.kwargs
        before = kwargs["before_value"]
        after = kwargs["after_value"]
        assert before == {"stream_ids": [101]}
        assert after == {"stream_ids": [101, 201]}
        # Credential safety: the provider token must not leak anywhere.
        serialized = repr(kwargs)
        assert "secret-token" not in serialized
        assert "http" not in serialized

    def test_journal_round_trip_recovers_channel_stream_pair(self):
        """The (channel_id, stream_id) pair is recoverable from the entry."""
        with patch("auto_creation_executor.journal.log_entry") as mock_log:
            self._run_live_merge(execution_id=42)

        kwargs = mock_log.call_args.kwargs
        channel_id = kwargs["entity_id"]
        before_ids = set(kwargs["before_value"]["stream_ids"])
        after_ids = set(kwargs["after_value"]["stream_ids"])
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
        with patch("auto_creation_executor.journal.log_entry") as mock_log:
            result = asyncio.get_event_loop().run_until_complete(
                executor.execute(action, self.stream_ctx, exec_ctx)
            )

        assert result.success is True
        assert "Would add" in result.description
        self.client.update_channel.assert_not_called()
        mock_log.assert_not_called()

    def test_multi_merge_run_writes_one_entry_per_merge(self):
        """A run merging N streams writes N entries, all sharing batch_id."""
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
        with patch("auto_creation_executor.journal.log_entry") as mock_log:
            for sc in stream_ctxs:
                asyncio.get_event_loop().run_until_complete(
                    executor.execute(action, sc, exec_ctx)
                )

        assert mock_log.call_count == 3
        batch_ids = {c.kwargs["batch_id"] for c in mock_log.call_args_list}
        assert batch_ids == {"7"}

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
        with patch("auto_creation_executor.journal.log_entry") as mock_log:
            result = asyncio.get_event_loop().run_until_complete(
                executor.execute(action, self.stream_ctx, exec_ctx)
            )

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
        with patch("auto_creation_executor.journal.log_entry") as mock_log:
            result = asyncio.get_event_loop().run_until_complete(
                executor.execute(action, stream_ctx, exec_ctx)
            )

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
             "channel_number": 100, "streams": []},
            {"id": 20, "name": "CNN", "tvg_id": "CNN.US",
             "channel_number": 200, "streams": []},
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
        with patch("auto_creation_executor.journal.log_entry"):
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
            {"id": 10, "name": "ESPN", "tvg_id": "", "channel_number": 100, "streams": []},
            {"id": 20, "name": "CNN", "tvg_id": "", "channel_number": 200, "streams": []},
            {"id": 30, "name": "FOX", "tvg_id": "", "channel_number": 300, "streams": []},
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
        with patch("auto_creation_executor.journal.log_entry"):
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
