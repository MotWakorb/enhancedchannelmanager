"""
Unit tests for the auto-creation executor service.

Tests the ActionExecutor class which executes actions against channels, groups,
and streams with proper rollback tracking.
"""
import pytest
from unittest.mock import MagicMock, AsyncMock
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

    def test_add_result_channel_modified(self):
        """add_result tracks modified channels."""
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

        assert ctx.channels_updated == 1
        assert len(ctx.modified_entities) == 1
        assert ctx.modified_entities[0]["previous"]["streams"] == [101]

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


class TestActionExecutorPropertyActions:
    """Tests for property assignment actions."""

    def setup_method(self):
        """Set up test fixtures."""
        self.client = MagicMock()
        self.client.update_channel = AsyncMock()

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
        self.client.update_channel.assert_called_with(1, {"logo_url": "http://example.com/espn.png"})

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
        self.client.update_channel.assert_called_with(1, {"logo_url": "http://other.com/logo.png"})

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
        """Assign EPG source."""
        action = {"type": "assign_epg", "epg_id": 5}  # Params at top level
        exec_ctx = ExecutionContext()
        exec_ctx.current_channel_id = 1

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute(action, self.stream_ctx, exec_ctx)
        )

        assert result.success is True
        self.client.update_channel.assert_called_with(1, {"epg_id": 5})

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
        assert call_args["name"] == "US: Sports"

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
