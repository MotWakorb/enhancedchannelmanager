"""
Auto-Creation Action Executor Service

Executes actions defined in auto-creation rules, such as creating channels,
groups, merging streams, and assigning properties. Tracks all changes for
potential rollback.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional
import re

from auto_creation_schema import Action, ActionType, TemplateVariables
from auto_creation_evaluator import StreamContext


logger = logging.getLogger(__name__)


@dataclass
class ActionResult:
    """Result of executing a single action."""
    success: bool
    action_type: str
    description: str
    entity_type: Optional[str] = None  # channel, group, stream
    entity_id: Optional[int] = None
    entity_name: Optional[str] = None
    created: bool = False  # True if new entity was created
    modified: bool = False  # True if existing entity was modified
    skipped: bool = False  # True if action was skipped (e.g., channel exists)
    previous_state: Optional[dict] = None  # For rollback
    error: Optional[str] = None


@dataclass
class ExecutionContext:
    """
    Context for action execution, accumulates results and tracks state.
    """
    # Execution mode
    dry_run: bool = False

    # Results tracking
    results: list[ActionResult] = field(default_factory=list)

    # Created/modified entities for rollback
    created_entities: list[dict] = field(default_factory=list)
    modified_entities: list[dict] = field(default_factory=list)

    # Statistics
    channels_created: int = 0
    channels_updated: int = 0
    groups_created: int = 0
    streams_merged: int = 0
    streams_skipped: int = 0

    # Current state (updated during execution)
    current_channel_id: Optional[int] = None  # Channel created/selected for this stream
    current_group_id: Optional[int] = None  # Group created/selected

    # Custom variables set by set_variable actions
    custom_variables: dict = field(default_factory=dict)

    def add_result(self, result: ActionResult):
        """Add an action result and update statistics."""
        self.results.append(result)

        if result.created:
            if result.entity_type == "channel":
                self.channels_created += 1
                self.created_entities.append({
                    "type": "channel",
                    "id": result.entity_id,
                    "name": result.entity_name
                })
            elif result.entity_type == "group":
                self.groups_created += 1
                self.created_entities.append({
                    "type": "group",
                    "id": result.entity_id,
                    "name": result.entity_name
                })

        if result.modified:
            if result.entity_type == "channel":
                self.channels_updated += 1
            self.modified_entities.append({
                "type": result.entity_type,
                "id": result.entity_id,
                "name": result.entity_name,
                "previous": result.previous_state
            })

        if result.skipped:
            self.streams_skipped += 1


class ActionExecutor:
    """
    Executes actions against the Dispatcharr API.

    Usage:
        executor = ActionExecutor(dispatcharr_client)
        ctx = ExecutionContext()
        result = await executor.execute(action, stream_context, ctx)
    """

    def __init__(self, client, existing_channels: list = None, existing_groups: list = None,
                 normalization_engine=None, settings=None, all_profile_ids: list = None):
        """
        Initialize the executor.

        Args:
            client: Dispatcharr API client
            existing_channels: List of existing channels (for lookup/merge)
            existing_groups: List of existing groups (for lookup)
            normalization_engine: Optional NormalizationEngine for name normalization
            settings: DispatcharrSettings instance for channel naming/profile defaults
            all_profile_ids: All channel profile IDs (for default profile assignment)
        """
        self.client = client
        self.existing_channels = existing_channels or []
        self.existing_groups = existing_groups or []
        self._normalization_engine = normalization_engine
        self._settings = settings
        self._all_profile_ids = all_profile_ids or []

        # Build lookup indices
        self._channel_by_id = {c["id"]: c for c in self.existing_channels}
        self._channel_by_name = {c["name"].lower(): c for c in self.existing_channels}
        self._group_by_id = {g["id"]: g for g in self.existing_groups}
        self._group_by_name = {g["name"].lower(): g for g in self.existing_groups}

        # Track newly created entities during this execution
        self._created_channels = {}  # name.lower() -> channel dict
        self._base_name_to_channel = {}  # base_name.lower() -> channel dict (for number-prefixed lookups)
        self._created_groups = {}  # name.lower() -> group dict
        self._logo_cache = {}  # logo_url -> logo_id

        # Channel number tracking
        self._used_channel_numbers = set()
        for c in self.existing_channels:
            if c.get("channel_number"):
                self._used_channel_numbers.add(c["channel_number"])

    async def execute(self, action: Action | dict, stream_ctx: StreamContext,
                      exec_ctx: ExecutionContext, rule_target_group_id: int = None,
                      normalize_names: bool = False) -> ActionResult:
        """
        Execute a single action.

        Args:
            action: Action to execute
            stream_ctx: Stream context with stream data
            exec_ctx: Execution context for tracking results
            rule_target_group_id: Default target group from rule

        Returns:
            ActionResult with execution details
        """
        if isinstance(action, dict):
            action = Action.from_dict(action)

        try:
            action_type = ActionType(action.type)
        except ValueError:
            return ActionResult(
                success=False,
                action_type=action.type,
                description=f"Unknown action type: {action.type}",
                error=f"Unknown action type: {action.type}"
            )

        # Build template context for variable expansion
        template_ctx = self._build_template_context(stream_ctx, exec_ctx)

        # Execute based on action type
        if action_type == ActionType.CREATE_CHANNEL:
            result = await self._execute_create_channel(
                action, stream_ctx, exec_ctx, template_ctx, rule_target_group_id,
                normalize_names=normalize_names
            )
        elif action_type == ActionType.CREATE_GROUP:
            result = await self._execute_create_group(action, stream_ctx, exec_ctx, template_ctx)
        elif action_type == ActionType.MERGE_STREAMS:
            result = await self._execute_merge_streams(action, stream_ctx, exec_ctx, template_ctx)
        elif action_type == ActionType.ASSIGN_LOGO:
            result = await self._execute_assign_logo(action, stream_ctx, exec_ctx)
        elif action_type == ActionType.ASSIGN_TVG_ID:
            result = await self._execute_assign_tvg_id(action, stream_ctx, exec_ctx)
        elif action_type == ActionType.ASSIGN_EPG:
            result = await self._execute_assign_epg(action, stream_ctx, exec_ctx)
        elif action_type == ActionType.ASSIGN_PROFILE:
            result = await self._execute_assign_profile(action, stream_ctx, exec_ctx)
        elif action_type == ActionType.SET_CHANNEL_NUMBER:
            result = await self._execute_set_channel_number(action, stream_ctx, exec_ctx)
        elif action_type == ActionType.SET_VARIABLE:
            result = await self._execute_set_variable(action, stream_ctx, exec_ctx, template_ctx)
        elif action_type == ActionType.SKIP:
            result = ActionResult(
                success=True,
                action_type=action.type,
                description="Stream skipped by rule",
                skipped=True
            )
        elif action_type == ActionType.STOP_PROCESSING:
            result = ActionResult(
                success=True,
                action_type=action.type,
                description="Stop processing further rules"
            )
        elif action_type == ActionType.LOG_MATCH:
            message = action.params.get("message", "Stream matched rule")
            expanded = TemplateVariables.expand_template(message, template_ctx, exec_ctx.custom_variables)
            logger.info(f"[AutoCreation] {expanded}")
            result = ActionResult(
                success=True,
                action_type=action.type,
                description=expanded
            )
        else:
            result = ActionResult(
                success=False,
                action_type=action.type,
                description=f"Unhandled action type: {action.type}",
                error=f"Unhandled action type"
            )

        exec_ctx.add_result(result)
        return result

    def _build_template_context(self, stream_ctx: StreamContext, exec_ctx: ExecutionContext = None) -> dict:
        """Build template variable context from stream context."""
        quality_str = None
        if stream_ctx.resolution_height:
            if stream_ctx.resolution_height >= 2160:
                quality_str = "4K"
            elif stream_ctx.resolution_height >= 1080:
                quality_str = "1080p"
            elif stream_ctx.resolution_height >= 720:
                quality_str = "720p"
            elif stream_ctx.resolution_height >= 480:
                quality_str = "480p"
            else:
                quality_str = f"{stream_ctx.resolution_height}p"

        ctx = {
            TemplateVariables.STREAM_NAME: stream_ctx.stream_name,
            TemplateVariables.STREAM_GROUP: stream_ctx.group_name or "",
            TemplateVariables.TVG_ID: stream_ctx.tvg_id or "",
            TemplateVariables.TVG_NAME: stream_ctx.tvg_name or "",
            TemplateVariables.QUALITY: quality_str or "",
            TemplateVariables.QUALITY_RAW: stream_ctx.resolution_height or "",
            TemplateVariables.PROVIDER: stream_ctx.m3u_account_name or "",
            TemplateVariables.PROVIDER_ID: stream_ctx.m3u_account_id or "",
            TemplateVariables.NORMALIZED_NAME: stream_ctx.normalized_name or stream_ctx.stream_name,
        }

        # Add custom variables with var: prefix
        if exec_ctx and exec_ctx.custom_variables:
            for var_name, value in exec_ctx.custom_variables.items():
                ctx[f"var:{var_name}"] = value

        return ctx

    # =========================================================================
    # Channel Creation
    # =========================================================================

    def _apply_name_transform(self, name: str, params: dict) -> str:
        """Apply optional regex name transform to a name string."""
        pattern = params.get("name_transform_pattern")
        if pattern:
            replacement = params.get("name_transform_replacement", "")
            # Convert JS-style backreferences ($1, $2) to Python (\1, \2)
            py_replacement = re.sub(r'\$(\d+)', r'\\\1', replacement)
            try:
                name = re.sub(pattern, py_replacement, name)
            except re.error as e:
                logger.warning(f"Name transform regex error: {e}")
        return name.strip()

    async def _resolve_logo_id(self, logo_url: str, name_hint: str = "") -> Optional[int]:
        """Resolve a logo URL to a Dispatcharr logo_id, creating if needed.

        Uses a cache to avoid duplicate lookups/creations within the same run.
        """
        if not logo_url:
            return None

        # Check cache first
        if logo_url in self._logo_cache:
            return self._logo_cache[logo_url]

        try:
            # Try to create the logo (Dispatcharr will reject duplicates)
            logo_name = name_hint or logo_url.split("/")[-1]
            result = await self.client.create_logo({"name": logo_name, "url": logo_url})
            logo_id = result.get("id")
            if logo_id:
                self._logo_cache[logo_url] = logo_id
                return logo_id
        except Exception as e:
            error_str = str(e).lower()
            # If logo already exists, find it by URL
            if "already exists" in error_str or "400" in error_str:
                try:
                    existing = await self.client.find_logo_by_url(logo_url)
                    if existing:
                        logo_id = existing.get("id")
                        self._logo_cache[logo_url] = logo_id
                        return logo_id
                except Exception as search_err:
                    logger.warning(f"Failed to find existing logo by URL: {search_err}")
            else:
                logger.warning(f"Failed to create logo from '{logo_url}': {e}")
        return None

    async def _execute_create_channel(self, action: Action, stream_ctx: StreamContext,
                                       exec_ctx: ExecutionContext, template_ctx: dict,
                                       rule_target_group_id: int = None,
                                       normalize_names: bool = False) -> ActionResult:
        """Execute create_channel action."""
        params = action.params
        name_template = params.get("name_template", "{stream_name}")
        channel_name = TemplateVariables.expand_template(name_template, template_ctx, exec_ctx.custom_variables)
        channel_name = self._apply_name_transform(channel_name, params)

        # Apply normalization engine if enabled
        if normalize_names and self._normalization_engine:
            try:
                norm_result = self._normalization_engine.normalize(channel_name)
                if norm_result.normalized != channel_name:
                    logger.debug(f"Normalized channel name: '{channel_name}' -> '{norm_result.normalized}'")
                    channel_name = norm_result.normalized
            except Exception as e:
                logger.warning(f"Failed to normalize channel name '{channel_name}': {e}")

        if_exists = params.get("if_exists", "skip")
        group_id = params.get("group_id") or exec_ctx.current_group_id or rule_target_group_id

        # Check if channel already exists (check with original name before number prefix)
        existing = self._find_channel_by_name(channel_name)

        if existing:
            if if_exists == "skip":
                exec_ctx.current_channel_id = existing["id"]
                return ActionResult(
                    success=True,
                    action_type=action.type,
                    description=f"Channel '{channel_name}' already exists, skipped",
                    entity_type="channel",
                    entity_id=existing["id"],
                    entity_name=channel_name,
                    skipped=True
                )
            elif if_exists == "merge":
                # Add stream to existing channel
                return await self._add_stream_to_channel(existing, stream_ctx, exec_ctx)
            elif if_exists == "update":
                # Update existing channel properties
                return await self._update_channel(existing, stream_ctx, exec_ctx, params)

        # Determine channel number first (needed for name prefix)
        channel_number = self._get_next_channel_number(params.get("channel_number", "auto"))

        # Apply channel number in name if setting is enabled
        base_name = channel_name  # Save before prefix for base-name mapping
        channel_name = self._apply_channel_number_in_name(channel_name, channel_number)

        # Create new channel
        if exec_ctx.dry_run:
            # Track simulated channel so subsequent streams in this run
            # see it as existing (matches execute-mode behavior)
            simulated = {"id": -1, "name": channel_name, "channel_number": channel_number,
                         "channel_group_id": group_id, "streams": [stream_ctx.stream_id]}
            self._created_channels[channel_name.lower()] = simulated
            # Map base name to prefixed channel so subsequent lookups by base name merge correctly
            if base_name.lower() != channel_name.lower():
                self._base_name_to_channel[base_name.lower()] = simulated
            self._used_channel_numbers.add(channel_number)
            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Would create channel '{channel_name}' (#{channel_number}) in group {group_id}",
                entity_type="channel",
                entity_name=channel_name,
                created=True
            )

        # Create channel via API
        try:
            channel_data = {
                "name": channel_name,
                "channel_number": channel_number,
                "channel_group_id": group_id,
                "streams": [stream_ctx.stream_id]
            }

            # Resolve logo URL to a Dispatcharr logo_id
            if stream_ctx.logo_url:
                logo_id = await self._resolve_logo_id(stream_ctx.logo_url, channel_name)
                if logo_id:
                    channel_data["logo_id"] = logo_id
            if stream_ctx.tvg_id:
                channel_data["tvg_id"] = stream_ctx.tvg_id

            new_channel = await self.client.create_channel(channel_data)

            # Track the new channel
            self._created_channels[channel_name.lower()] = new_channel
            # Map base name to prefixed channel so subsequent lookups by base name merge correctly
            if base_name.lower() != channel_name.lower():
                self._base_name_to_channel[base_name.lower()] = new_channel
            self._used_channel_numbers.add(channel_number)
            exec_ctx.current_channel_id = new_channel["id"]

            # Assign default channel profiles if configured
            profile_desc = await self._assign_default_profiles(new_channel["id"])

            desc = f"Created channel '{channel_name}' (#{channel_number})"
            if profile_desc:
                desc += f", {profile_desc}"

            return ActionResult(
                success=True,
                action_type=action.type,
                description=desc,
                entity_type="channel",
                entity_id=new_channel["id"],
                entity_name=channel_name,
                created=True
            )

        except Exception as e:
            logger.error(f"Failed to create channel '{channel_name}': {e}")
            return ActionResult(
                success=False,
                action_type=action.type,
                description=f"Failed to create channel '{channel_name}'",
                error=str(e)
            )

    async def _add_stream_to_channel(self, channel: dict, stream_ctx: StreamContext,
                                      exec_ctx: ExecutionContext) -> ActionResult:
        """Add a stream to an existing channel (merge behavior)."""
        channel_id = channel["id"]
        channel_name = channel["name"]

        # Get current streams
        current_streams = [s["id"] if isinstance(s, dict) else s for s in channel.get("streams", [])]

        if stream_ctx.stream_id in current_streams:
            exec_ctx.current_channel_id = channel_id
            return ActionResult(
                success=True,
                action_type="merge_stream",
                description=f"Stream already in channel '{channel_name}'",
                entity_type="channel",
                entity_id=channel_id,
                entity_name=channel_name,
                skipped=True
            )

        if exec_ctx.dry_run:
            # Update cached channel so subsequent dry-run merges see this stream
            channel["streams"] = current_streams + [stream_ctx.stream_id]
            exec_ctx.current_channel_id = channel_id
            return ActionResult(
                success=True,
                action_type="merge_stream",
                description=f"Would add stream to channel '{channel_name}'",
                entity_type="channel",
                entity_id=channel_id,
                entity_name=channel_name,
                modified=True
            )

        try:
            # Save previous state for rollback
            previous_state = {
                "streams": current_streams.copy()
            }

            # Add stream
            new_streams = current_streams + [stream_ctx.stream_id]
            await self.client.update_channel(channel_id, {"streams": new_streams})

            # Update cached channel so subsequent merges see the full list
            channel["streams"] = new_streams
            exec_ctx.current_channel_id = channel_id

            return ActionResult(
                success=True,
                action_type="merge_stream",
                description=f"Added stream to channel '{channel_name}'",
                entity_type="channel",
                entity_id=channel_id,
                entity_name=channel_name,
                modified=True,
                previous_state=previous_state
            )

        except Exception as e:
            logger.error(f"Failed to add stream to channel '{channel_name}': {e}")
            return ActionResult(
                success=False,
                action_type="merge_stream",
                description=f"Failed to add stream to channel",
                error=str(e)
            )

    async def _update_channel(self, channel: dict, stream_ctx: StreamContext,
                               exec_ctx: ExecutionContext, params: dict) -> ActionResult:
        """Update an existing channel's properties."""
        channel_id = channel["id"]
        channel_name = channel["name"]

        if exec_ctx.dry_run:
            return ActionResult(
                success=True,
                action_type="update_channel",
                description=f"Would update channel '{channel_name}'",
                entity_type="channel",
                entity_id=channel_id,
                entity_name=channel_name,
                modified=True
            )

        try:
            # Save previous state
            previous_state = {
                "logo_url": channel.get("logo_url"),
                "tvg_id": channel.get("tvg_id")
            }

            updates = {}
            if stream_ctx.logo_url and not channel.get("logo_url"):
                updates["logo_url"] = stream_ctx.logo_url
            if stream_ctx.tvg_id and not channel.get("tvg_id"):
                updates["tvg_id"] = stream_ctx.tvg_id

            if updates:
                await self.client.update_channel(channel_id, updates)

            exec_ctx.current_channel_id = channel_id

            return ActionResult(
                success=True,
                action_type="update_channel",
                description=f"Updated channel '{channel_name}'",
                entity_type="channel",
                entity_id=channel_id,
                entity_name=channel_name,
                modified=bool(updates),
                previous_state=previous_state
            )

        except Exception as e:
            logger.error(f"Failed to update channel '{channel_name}': {e}")
            return ActionResult(
                success=False,
                action_type="update_channel",
                description=f"Failed to update channel",
                error=str(e)
            )

    # =========================================================================
    # Group Creation
    # =========================================================================

    async def _execute_create_group(self, action: Action, stream_ctx: StreamContext,
                                     exec_ctx: ExecutionContext, template_ctx: dict) -> ActionResult:
        """Execute create_group action."""
        params = action.params
        name_template = params.get("name_template", "{stream_group}")
        group_name = TemplateVariables.expand_template(name_template, template_ctx, exec_ctx.custom_variables)
        group_name = self._apply_name_transform(group_name, params)
        if_exists = params.get("if_exists", "use_existing")

        if not group_name:
            return ActionResult(
                success=False,
                action_type=action.type,
                description="Group name is empty after template expansion",
                error="Empty group name"
            )

        # Check if group already exists
        existing = self._find_group_by_name(group_name)

        if existing:
            if if_exists == "use_existing":
                exec_ctx.current_group_id = existing["id"]
                return ActionResult(
                    success=True,
                    action_type=action.type,
                    description=f"Using existing group '{group_name}'",
                    entity_type="group",
                    entity_id=existing["id"],
                    entity_name=group_name,
                    skipped=True
                )
            else:  # skip
                exec_ctx.current_group_id = existing["id"]
                return ActionResult(
                    success=True,
                    action_type=action.type,
                    description=f"Group '{group_name}' already exists, skipped",
                    entity_type="group",
                    entity_id=existing["id"],
                    entity_name=group_name,
                    skipped=True
                )

        # Create new group
        if exec_ctx.dry_run:
            # Track simulated group so subsequent streams see it as existing
            simulated = {"id": -1, "name": group_name}
            self._created_groups[group_name.lower()] = simulated
            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Would create group '{group_name}'",
                entity_type="group",
                entity_name=group_name,
                created=True
            )

        try:
            new_group = await self.client.create_channel_group(group_name)

            # Track the new group
            self._created_groups[group_name.lower()] = new_group
            exec_ctx.current_group_id = new_group["id"]

            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Created group '{group_name}'",
                entity_type="group",
                entity_id=new_group["id"],
                entity_name=group_name,
                created=True
            )

        except Exception as e:
            logger.error(f"Failed to create group '{group_name}': {e}")
            return ActionResult(
                success=False,
                action_type=action.type,
                description=f"Failed to create group '{group_name}'",
                error=str(e)
            )

    # =========================================================================
    # Stream Merging
    # =========================================================================

    async def _execute_merge_streams(self, action: Action, stream_ctx: StreamContext,
                                      exec_ctx: ExecutionContext, template_ctx: dict) -> ActionResult:
        """Execute merge_streams action."""
        params = action.params
        target = params.get("target", "auto")
        find_channel_by = params.get("find_channel_by")
        find_channel_value = params.get("find_channel_value")

        # For existing_channel target, find the channel
        if target == "existing_channel" or target == "auto":
            channel = None

            if find_channel_by == "name_exact":
                expanded_name = TemplateVariables.expand_template(find_channel_value or "", template_ctx, exec_ctx.custom_variables)
                channel = self._find_channel_by_name(expanded_name)
            elif find_channel_by == "name_regex":
                channel = self._find_channel_by_regex(find_channel_value)
            elif find_channel_by == "tvg_id":
                channel = self._find_channel_by_tvg_id(find_channel_value or stream_ctx.tvg_id)

            if channel:
                return await self._add_stream_to_channel(channel, stream_ctx, exec_ctx)
            elif target == "existing_channel":
                return ActionResult(
                    success=False,
                    action_type=action.type,
                    description=f"No channel found matching {find_channel_by}='{find_channel_value}'",
                    error="Channel not found for merge"
                )
            # For auto target, fall through to create new channel

        # Create new channel with this stream
        # This is handled by the orchestrator creating a create_channel action
        return ActionResult(
            success=True,
            action_type=action.type,
            description="No existing channel found, will create new",
            skipped=True
        )

    # =========================================================================
    # Property Assignment Actions
    # =========================================================================

    async def _execute_assign_logo(self, action: Action, stream_ctx: StreamContext,
                                    exec_ctx: ExecutionContext) -> ActionResult:
        """Execute assign_logo action."""
        if not exec_ctx.current_channel_id:
            return ActionResult(
                success=False,
                action_type=action.type,
                description="No channel context for assign_logo",
                error="No channel to update"
            )

        value = action.params.get("value", "from_stream")
        logo_url = stream_ctx.logo_url if value == "from_stream" else value

        if not logo_url:
            return ActionResult(
                success=True,
                action_type=action.type,
                description="No logo URL to assign",
                skipped=True
            )

        if exec_ctx.dry_run:
            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Would assign logo: {logo_url[:50]}...",
                entity_type="channel",
                entity_id=exec_ctx.current_channel_id,
                modified=True
            )

        try:
            channel = self._channel_by_id.get(exec_ctx.current_channel_id, {})
            previous_state = {"logo_url": channel.get("logo_url")}

            await self.client.update_channel(exec_ctx.current_channel_id, {"logo_url": logo_url})

            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Assigned logo to channel",
                entity_type="channel",
                entity_id=exec_ctx.current_channel_id,
                modified=True,
                previous_state=previous_state
            )
        except Exception as e:
            return ActionResult(
                success=False,
                action_type=action.type,
                description="Failed to assign logo",
                error=str(e)
            )

    async def _execute_assign_tvg_id(self, action: Action, stream_ctx: StreamContext,
                                      exec_ctx: ExecutionContext) -> ActionResult:
        """Execute assign_tvg_id action."""
        if not exec_ctx.current_channel_id:
            return ActionResult(
                success=False,
                action_type=action.type,
                description="No channel context for assign_tvg_id",
                error="No channel to update"
            )

        value = action.params.get("value", "from_stream")
        tvg_id = stream_ctx.tvg_id if value == "from_stream" else value

        if not tvg_id:
            return ActionResult(
                success=True,
                action_type=action.type,
                description="No tvg_id to assign",
                skipped=True
            )

        if exec_ctx.dry_run:
            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Would assign tvg_id: {tvg_id}",
                entity_type="channel",
                entity_id=exec_ctx.current_channel_id,
                modified=True
            )

        try:
            channel = self._channel_by_id.get(exec_ctx.current_channel_id, {})
            previous_state = {"tvg_id": channel.get("tvg_id")}

            await self.client.update_channel(exec_ctx.current_channel_id, {"tvg_id": tvg_id})

            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Assigned tvg_id '{tvg_id}' to channel",
                entity_type="channel",
                entity_id=exec_ctx.current_channel_id,
                modified=True,
                previous_state=previous_state
            )
        except Exception as e:
            return ActionResult(
                success=False,
                action_type=action.type,
                description="Failed to assign tvg_id",
                error=str(e)
            )

    async def _execute_assign_epg(self, action: Action, stream_ctx: StreamContext,
                                   exec_ctx: ExecutionContext) -> ActionResult:
        """Execute assign_epg action."""
        if not exec_ctx.current_channel_id:
            return ActionResult(
                success=False,
                action_type=action.type,
                description="No channel context for assign_epg",
                error="No channel to update"
            )

        epg_id = action.params.get("epg_id")
        if not epg_id:
            return ActionResult(
                success=False,
                action_type=action.type,
                description="No epg_id specified",
                error="Missing epg_id"
            )

        if exec_ctx.dry_run:
            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Would assign EPG source {epg_id}",
                entity_type="channel",
                entity_id=exec_ctx.current_channel_id,
                modified=True
            )

        try:
            channel = self._channel_by_id.get(exec_ctx.current_channel_id, {})
            previous_state = {"epg_id": channel.get("epg_id")}

            await self.client.update_channel(exec_ctx.current_channel_id, {"epg_id": epg_id})

            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Assigned EPG source {epg_id} to channel",
                entity_type="channel",
                entity_id=exec_ctx.current_channel_id,
                modified=True,
                previous_state=previous_state
            )
        except Exception as e:
            return ActionResult(
                success=False,
                action_type=action.type,
                description="Failed to assign EPG",
                error=str(e)
            )

    async def _execute_assign_profile(self, action: Action, stream_ctx: StreamContext,
                                       exec_ctx: ExecutionContext) -> ActionResult:
        """Execute assign_profile action."""
        if not exec_ctx.current_channel_id:
            return ActionResult(
                success=False,
                action_type=action.type,
                description="No channel context for assign_profile",
                error="No channel to update"
            )

        profile_id = action.params.get("profile_id")
        if not profile_id:
            return ActionResult(
                success=False,
                action_type=action.type,
                description="No profile_id specified",
                error="Missing profile_id"
            )

        if exec_ctx.dry_run:
            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Would assign stream profile {profile_id}",
                entity_type="channel",
                entity_id=exec_ctx.current_channel_id,
                modified=True
            )

        try:
            channel = self._channel_by_id.get(exec_ctx.current_channel_id, {})
            previous_state = {"stream_profile_id": channel.get("stream_profile_id")}

            await self.client.update_channel(exec_ctx.current_channel_id, {"stream_profile_id": profile_id})

            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Assigned stream profile {profile_id} to channel",
                entity_type="channel",
                entity_id=exec_ctx.current_channel_id,
                modified=True,
                previous_state=previous_state
            )
        except Exception as e:
            return ActionResult(
                success=False,
                action_type=action.type,
                description="Failed to assign profile",
                error=str(e)
            )

    async def _execute_set_channel_number(self, action: Action, stream_ctx: StreamContext,
                                           exec_ctx: ExecutionContext) -> ActionResult:
        """Execute set_channel_number action."""
        if not exec_ctx.current_channel_id:
            return ActionResult(
                success=False,
                action_type=action.type,
                description="No channel context for set_channel_number",
                error="No channel to update"
            )

        value = action.params.get("value", "auto")
        channel_number = self._get_next_channel_number(value)

        if exec_ctx.dry_run:
            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Would set channel number to {channel_number}",
                entity_type="channel",
                entity_id=exec_ctx.current_channel_id,
                modified=True
            )

        try:
            channel = self._channel_by_id.get(exec_ctx.current_channel_id, {})
            previous_state = {"channel_number": channel.get("channel_number")}

            await self.client.update_channel(exec_ctx.current_channel_id, {"channel_number": channel_number})
            self._used_channel_numbers.add(channel_number)

            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Set channel number to {channel_number}",
                entity_type="channel",
                entity_id=exec_ctx.current_channel_id,
                modified=True,
                previous_state=previous_state
            )
        except Exception as e:
            return ActionResult(
                success=False,
                action_type=action.type,
                description="Failed to set channel number",
                error=str(e)
            )

    # =========================================================================
    # Set Variable
    # =========================================================================

    async def _execute_set_variable(self, action: Action, stream_ctx: StreamContext,
                                     exec_ctx: ExecutionContext, template_ctx: dict) -> ActionResult:
        """Execute set_variable action."""
        params = action.params
        var_name = params.get("variable_name", "")
        mode = params.get("variable_mode", "literal")

        # Get source value for regex modes
        source_value = ""
        if mode in ("regex_extract", "regex_replace"):
            source_field = params.get("source_field", "stream_name")
            source_value = template_ctx.get(source_field, "")

        try:
            if mode == "regex_extract":
                pattern = params.get("pattern", "")
                match = re.search(pattern, str(source_value))
                if match and match.groups():
                    result_value = match.group(1)
                elif match:
                    result_value = match.group(0)
                else:
                    result_value = ""

            elif mode == "regex_replace":
                pattern = params.get("pattern", "")
                replacement = params.get("replacement", "")
                # Convert JS-style backreferences ($1, $2) to Python (\1, \2)
                py_replacement = re.sub(r'\$(\d+)', r'\\\1', replacement)
                result_value = re.sub(pattern, py_replacement, str(source_value))

            elif mode == "literal":
                template = params.get("template", "")
                result_value = TemplateVariables.expand_template(template, template_ctx, exec_ctx.custom_variables)

            else:
                return ActionResult(
                    success=False,
                    action_type=action.type,
                    description=f"Unknown variable mode: {mode}",
                    error=f"Unknown variable mode: {mode}"
                )

            # Store variable in execution context
            exec_ctx.custom_variables[var_name] = result_value

            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Set variable '{var_name}' = '{result_value}'"
            )

        except re.error as e:
            return ActionResult(
                success=False,
                action_type=action.type,
                description=f"Regex error in set_variable: {e}",
                error=str(e)
            )

    # =========================================================================
    # Reconciliation / Cleanup Methods
    # =========================================================================

    async def remove_channel(self, channel_id: int) -> ActionResult:
        """Delete an orphaned channel via the Dispatcharr API."""
        try:
            # Look up channel name for logging
            channel = self._channel_by_id.get(channel_id, {})
            channel_name = channel.get("name", f"ID:{channel_id}")

            await self.client.delete_channel(channel_id)
            logger.info(f"[Reconcile] Deleted orphaned channel {channel_id} ({channel_name})")

            return ActionResult(
                success=True,
                action_type="remove_channel",
                description=f"Deleted orphaned channel '{channel_name}'",
                entity_type="channel",
                entity_id=channel_id,
                entity_name=channel_name,
            )
        except Exception as e:
            error_str = str(e)
            # Channel already gone (404) - treat as success
            if "404" in error_str or "not found" in error_str.lower():
                logger.info(f"[Reconcile] Channel {channel_id} already deleted (404)")
                return ActionResult(
                    success=True,
                    action_type="remove_channel",
                    description=f"Channel {channel_id} already deleted",
                    entity_type="channel",
                    entity_id=channel_id,
                )
            logger.error(f"[Reconcile] Failed to delete channel {channel_id}: {e}")
            return ActionResult(
                success=False,
                action_type="remove_channel",
                description=f"Failed to delete channel {channel_id}",
                error=error_str,
            )

    async def move_channel_to_uncategorized(self, channel_id: int) -> ActionResult:
        """Move an orphaned channel to the Uncategorized group (group_id=None)."""
        try:
            channel = self._channel_by_id.get(channel_id, {})
            channel_name = channel.get("name", f"ID:{channel_id}")

            await self.client.update_channel(channel_id, {"channel_group_id": None})
            logger.info(f"[Reconcile] Moved orphaned channel {channel_id} ({channel_name}) to Uncategorized")

            return ActionResult(
                success=True,
                action_type="move_channel",
                description=f"Moved orphaned channel '{channel_name}' to Uncategorized",
                entity_type="channel",
                entity_id=channel_id,
                entity_name=channel_name,
                modified=True,
            )
        except Exception as e:
            error_str = str(e)
            if "404" in error_str or "not found" in error_str.lower():
                logger.info(f"[Reconcile] Channel {channel_id} already deleted (404)")
                return ActionResult(
                    success=True,
                    action_type="move_channel",
                    description=f"Channel {channel_id} already deleted",
                    entity_type="channel",
                    entity_id=channel_id,
                )
            logger.error(f"[Reconcile] Failed to move channel {channel_id}: {e}")
            return ActionResult(
                success=False,
                action_type="move_channel",
                description=f"Failed to move channel {channel_id}",
                error=error_str,
            )

    async def delete_group_if_empty(self, group_id: int) -> ActionResult:
        """Delete a channel group if it has no channels."""
        try:
            group = self._group_by_id.get(group_id, {})
            group_name = group.get("name", f"ID:{group_id}")

            # Fetch current channels in the group
            all_channels = []
            page = 1
            while True:
                result = await self.client.get_channels(page=page, page_size=100)
                channels = result.get("results", [])
                all_channels.extend(channels)
                if len(all_channels) >= result.get("count", 0) or not channels:
                    break
                page += 1

            channels_in_group = [c for c in all_channels if c.get("channel_group") == group_id]

            if channels_in_group:
                return ActionResult(
                    success=True,
                    action_type="delete_empty_group",
                    description=f"Group '{group_name}' still has {len(channels_in_group)} channels, kept",
                    entity_type="group",
                    entity_id=group_id,
                    entity_name=group_name,
                    skipped=True,
                )

            await self.client.delete_channel_group(group_id)
            logger.info(f"[Reconcile] Deleted empty group {group_id} ({group_name})")

            return ActionResult(
                success=True,
                action_type="delete_empty_group",
                description=f"Deleted empty group '{group_name}'",
                entity_type="group",
                entity_id=group_id,
                entity_name=group_name,
            )
        except Exception as e:
            error_str = str(e)
            if "404" in error_str or "not found" in error_str.lower():
                return ActionResult(
                    success=True,
                    action_type="delete_empty_group",
                    description=f"Group {group_id} already deleted",
                    entity_type="group",
                    entity_id=group_id,
                )
            logger.error(f"[Reconcile] Failed to delete group {group_id}: {e}")
            return ActionResult(
                success=False,
                action_type="delete_empty_group",
                description=f"Failed to delete group {group_id}",
                error=error_str,
            )

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _apply_channel_number_in_name(self, channel_name: str, channel_number: int) -> str:
        """Prepend channel number to name if settings.include_channel_number_in_name is enabled."""
        if not self._settings or not getattr(self._settings, 'include_channel_number_in_name', False):
            return channel_name

        separator = getattr(self._settings, 'channel_number_separator', '-') or '-'
        number_str = str(int(channel_number) if channel_number == int(channel_number) else channel_number)

        # Strip any existing number prefix (e.g., "4000 | USA Network" -> "USA Network")
        stripped = re.sub(r'^\d+(?:\.\d+)?\s*[|\-:]\s*', '', channel_name).strip()
        if not stripped:
            stripped = channel_name

        result = f"{number_str} {separator} {stripped}"
        if result != channel_name:
            logger.debug(f"[Channel-number-in-name] '{channel_name}' -> '{result}'")
        return result

    async def _assign_default_profiles(self, channel_id: int) -> str:
        """Assign default channel profiles to a newly created channel.

        Enables channel in default profiles, disables in non-default profiles.
        Returns a description string for logging, or empty string if no profiles configured.
        """
        if not self._settings or not self._settings.default_channel_profile_ids:
            return ""
        if not self._all_profile_ids:
            return ""

        default_ids = set(self._settings.default_channel_profile_ids)
        enabled_count = 0
        disabled_count = 0

        for pid in self._all_profile_ids:
            try:
                if pid in default_ids:
                    await self.client.update_profile_channel(pid, channel_id, {"enabled": True})
                    enabled_count += 1
                else:
                    await self.client.update_profile_channel(pid, channel_id, {"enabled": False})
                    disabled_count += 1
            except Exception as e:
                logger.warning(f"[Profile-assign] Failed to update profile {pid} for channel {channel_id}: {e}")

        if enabled_count or disabled_count:
            desc = f"profiles: enabled in {enabled_count}, disabled in {disabled_count}"
            logger.info(f"[Profile-assign] Channel {channel_id}: {desc}")
            return desc
        return ""

    def _find_channel_by_name(self, name: str) -> Optional[dict]:
        """Find channel by exact name (case-insensitive).

        Also checks the base-name mapping so that a lookup for "USA Network"
        finds a channel created as "4000 | USA Network".
        """
        name_lower = name.lower()
        # Check newly created channels first (by exact name)
        if name_lower in self._created_channels:
            return self._created_channels[name_lower]
        # Check base-name mapping (base name -> number-prefixed channel)
        if name_lower in self._base_name_to_channel:
            return self._base_name_to_channel[name_lower]
        return self._channel_by_name.get(name_lower)

    def _find_channel_by_regex(self, pattern: str) -> Optional[dict]:
        """Find first channel matching regex pattern."""
        try:
            regex = re.compile(pattern, re.IGNORECASE)
            for channel in self.existing_channels:
                if regex.search(channel.get("name", "")):
                    return channel
            for channel in self._created_channels.values():
                if regex.search(channel.get("name", "")):
                    return channel
        except re.error:
            pass
        return None

    def _find_channel_by_tvg_id(self, tvg_id: str) -> Optional[dict]:
        """Find channel by TVG ID."""
        if not tvg_id:
            return None
        for channel in self.existing_channels:
            if channel.get("tvg_id") == tvg_id:
                return channel
        for channel in self._created_channels.values():
            if channel.get("tvg_id") == tvg_id:
                return channel
        return None

    def _find_group_by_name(self, name: str) -> Optional[dict]:
        """Find group by exact name (case-insensitive)."""
        name_lower = name.lower()
        # Check newly created groups first
        if name_lower in self._created_groups:
            return self._created_groups[name_lower]
        return self._group_by_name.get(name_lower)

    def _get_next_channel_number(self, spec: Any) -> int:
        """
        Get next available channel number based on spec.

        Args:
            spec: "auto", specific int, or "min-max" range string

        Returns:
            Next available channel number
        """
        if isinstance(spec, int):
            return spec

        if isinstance(spec, str):
            if spec == "auto":
                # Find next available number starting from 1
                num = 1
                while num in self._used_channel_numbers:
                    num += 1
                return num

            # Check for range format "min-max"
            match = re.match(r"^(\d+)-(\d+)$", spec)
            if match:
                min_num = int(match.group(1))
                max_num = int(match.group(2))
                for num in range(min_num, max_num + 1):
                    if num not in self._used_channel_numbers:
                        return num
                # Range exhausted, use next after max
                return max_num + 1

            # Try parsing as int
            try:
                return int(spec)
            except ValueError:
                pass

        # Fallback to auto
        num = 1
        while num in self._used_channel_numbers:
            num += 1
        return num
