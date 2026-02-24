"""
Auto-Creation Action Executor Service

Executes actions defined in auto-creation rules, such as creating channels,
groups, merging streams, and assigning properties. Tracks all changes for
potential rollback.
"""
import logging
from dataclasses import dataclass, field
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
    details: list[str] = field(default_factory=list)  # Additional context (normalization, group, etc.)
    deferred: bool = False  # True if action will be retried after EPG refresh


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
    streams_removed: int = 0

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
            elif result.entity_type == "stream" and result.action_type == "remove_from_channel":
                self.streams_removed += 1
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
                 normalization_engine=None, settings=None, all_profile_ids: list = None,
                 epg_data: list = None, epg_sources: list = None):
        """
        Initialize the executor.

        Args:
            client: Dispatcharr API client
            existing_channels: List of existing channels (for lookup/merge)
            existing_groups: List of existing groups (for lookup)
            normalization_engine: Optional NormalizationEngine for name normalization
            settings: DispatcharrSettings instance for channel naming/profile defaults
            all_profile_ids: All channel profile IDs (for default profile assignment)
            epg_data: EPG data entries from Dispatcharr (for assign_epg resolution)
            epg_sources: EPG source dicts from Dispatcharr (for dummy EPG detection)
        """
        self.client = client
        self.existing_channels = existing_channels or []
        self.existing_groups = existing_groups or []
        self._normalization_engine = normalization_engine
        self._settings = settings
        self._all_profile_ids = all_profile_ids or []

        # Build EPG data lookup: epg_source_id -> list of data entries
        self._epg_data_by_source: dict[int, list[dict]] = {}
        for entry in (epg_data or []):
            src_id = entry.get("epg_source")
            if src_id is not None:
                self._epg_data_by_source.setdefault(src_id, []).append(entry)

        # Identify dummy EPG sources (URL contains /api/dummy-epg/xmltv)
        self._dummy_epg_source_ids: set[int] = set()
        for src in (epg_sources or []):
            url = src.get("url") or ""
            if "/api/dummy-epg/xmltv" in url:
                self._dummy_epg_source_ids.add(src["id"])

        # Deferred EPG assignments (populated when dummy source has no data yet)
        self._deferred_epg_assignments: list[tuple] = []  # (channel_id, action, stream_ctx, exec_ctx)

        # Build lookup indices
        self._channel_by_id = {c["id"]: c for c in self.existing_channels}
        self._channel_by_name = {c["name"].lower(): c for c in self.existing_channels}
        self._group_by_id = {g["id"]: g for g in self.existing_groups}
        self._group_by_name = {g["name"].lower(): g for g in self.existing_groups}

        # Track newly created entities during this execution
        self._created_channels = {}  # name.lower() -> channel dict
        self._base_name_to_channel = {}  # base_name.lower() -> channel dict (for number-prefixed lookups)
        self._created_groups = {}  # name.lower() -> group dict
        self._next_dry_run_id = -1  # Unique negative IDs for dry-run simulated entities

        # Track streams per (channel_id, m3u_account_id) for max_streams_per_channel limit.
        # Lazily seeded per-channel via _ensure_channel_m3u_counts() because the
        # paginated channels API only returns stream IDs (ints), not full dicts.
        self._channel_m3u_counts: dict[tuple[int, int], int] = {}
        self._seeded_channels: set[int] = set()

        # Pre-populate base-name mapping for existing channels with "NUMBER | " prefixes
        _num_prefix = re.compile(r'^\d+\s*\|\s*')
        for c in self.existing_channels:
            stripped = _num_prefix.sub('', c["name"])
            if stripped != c["name"]:
                self._base_name_to_channel.setdefault(stripped.lower(), c)

        # Pre-populate normalized-name mapping so merge_streams auto-lookup
        # can find channels the same way normalized_name_in_group does
        self._normalized_name_to_channel: dict[str, dict] = {}
        if self._normalization_engine:
            for c in self.existing_channels:
                stripped = _num_prefix.sub('', c["name"])
                try:
                    result = self._normalization_engine.normalize(stripped)
                    if result.normalized and result.normalized.lower() != stripped.lower():
                        self._normalized_name_to_channel.setdefault(
                            result.normalized.lower(), c
                        )
                except Exception as e:
                    logger.warning("[AUTO-CREATE-EXEC] Normalization failed for channel '%s': %s", c.get("name", ""), e)

        # Pre-populate core-name mapping so merge_streams can fall back
        # to tag-group-based stripping (country prefix + quality suffix)
        # even when normalization rules are disabled.
        self._core_name_to_channel: dict[str, dict] = {}
        if self._normalization_engine:
            for c in self.existing_channels:
                stripped = _num_prefix.sub('', c["name"])
                try:
                    core = self._normalization_engine.extract_core_name(stripped)
                    if core:
                        self._core_name_to_channel.setdefault(core.lower(), c)
                except Exception as e:
                    logger.warning("[AUTO-CREATE-EXEC] Core name extraction failed for channel '%s': %s", c.get("name", ""), e)

        # Index deparenthesized variants of core names so that
        # "Bravo (East)" also matches channel "Bravo East" and vice versa.
        for core_key, ch_val in list(self._core_name_to_channel.items()):
            deparen = re.sub(r'\(([^)]+)\)', r'\1', core_key)
            deparen = re.sub(r'\s+', ' ', deparen).strip()
            if deparen != core_key:
                self._core_name_to_channel.setdefault(deparen, ch_val)

        # Pre-populate call-sign mapping so merge_streams can match
        # local affiliates by FCC call sign (W/K + 2-3 letters).
        self._callsign_to_channel: dict[str, dict] = {}
        if self._normalization_engine:
            for c in self.existing_channels:
                try:
                    cs = self._normalization_engine.extract_call_sign(c["name"])
                    if cs:
                        self._callsign_to_channel.setdefault(cs, c)
                except Exception as e:
                    logger.warning("[AUTO-CREATE-EXEC] Call sign extraction failed for channel '%s': %s", c.get("name", ""), e)

        self._logo_cache = {}  # logo_url -> logo_id

        # Channel number tracking
        self._used_channel_numbers = set()
        for c in self.existing_channels:
            if c.get("channel_number"):
                self._used_channel_numbers.add(c["channel_number"])
        self._channel_assigned_numbers = {}  # channel_id -> number (set_channel_number dedup)

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

        logger.debug(
            "[AUTO-CREATE-EXEC] Executing action type=%s for stream=%r "
            "(id=%s) dry_run=%s params=%s",
            action.type, stream_ctx.stream_name,
            stream_ctx.stream_id, exec_ctx.dry_run, action.params
        )

        try:
            action_type = ActionType(action.type)
        except ValueError:
            logger.debug("[AUTO-CREATE-EXEC] Unknown action type: %s", action.type)
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
            result = await self._execute_merge_streams(action, stream_ctx, exec_ctx, template_ctx,
                                                         normalize_names=normalize_names)
        elif action_type == ActionType.ASSIGN_LOGO:
            result = await self._execute_assign_logo(action, stream_ctx, exec_ctx, template_ctx)
        elif action_type == ActionType.ASSIGN_TVG_ID:
            result = await self._execute_assign_tvg_id(action, stream_ctx, exec_ctx, template_ctx)
        elif action_type == ActionType.ASSIGN_EPG:
            result = await self._execute_assign_epg(action, stream_ctx, exec_ctx)
        elif action_type == ActionType.ASSIGN_PROFILE:
            result = await self._execute_assign_profile(action, stream_ctx, exec_ctx)
        elif action_type == ActionType.SET_CHANNEL_NUMBER:
            result = await self._execute_set_channel_number(action, stream_ctx, exec_ctx)
        elif action_type == ActionType.SET_VARIABLE:
            result = await self._execute_set_variable(action, stream_ctx, exec_ctx, template_ctx)
        elif action_type == ActionType.REMOVE_FROM_CHANNEL:
            result = await self._execute_remove_from_channel(action, stream_ctx, exec_ctx)
        elif action_type == ActionType.SET_STREAM_PRIORITY:
            result = await self._execute_set_stream_priority(action, stream_ctx, exec_ctx)
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
            logger.info("[AUTO-CREATE-EXEC] %s", expanded)
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

        logger.debug(
            "[AUTO-CREATE-EXEC] Result: type=%s success=%s "
            "created=%s modified=%s skipped=%s "
            "desc=%r",
            result.action_type, result.success,
            result.created, result.modified, result.skipped,
            result.description
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

        logger.debug("[AUTO-CREATE-EXEC] Built context: %s", ctx)
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
                original = name
                name = re.sub(pattern, py_replacement, name)
                if name != original:
                    logger.debug("[AUTO-CREATE-EXEC] '%s' -> '%s' (pattern=/%s/ replacement='%s')", original, name, pattern, replacement)
            except re.error as e:
                logger.warning("[AUTO-CREATE-EXEC] Name transform regex error: %s", e)
        return name.strip()

    async def _resolve_logo_id(self, logo_url: str, name_hint: str = "") -> Optional[int]:
        """Resolve a logo URL to a Dispatcharr logo_id, creating if needed.

        Uses a cache to avoid duplicate lookups/creations within the same run.
        """
        if not logo_url:
            return None

        # Check cache first
        if logo_url in self._logo_cache:
            logger.debug("[AUTO-CREATE-EXEC] Cache hit for '%s' -> id=%s", logo_url[:60], self._logo_cache[logo_url])
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
                    logger.warning("[AUTO-CREATE-EXEC] Failed to find existing logo by URL: %s", search_err)
            else:
                logger.warning("[AUTO-CREATE-EXEC] Failed to create logo from '%s': %s", logo_url, e)
        return None

    def _get_group_name(self, group_id) -> Optional[str]:
        """Resolve a group ID to its name."""
        if not group_id:
            return None
        group = self._group_by_id.get(group_id) or self._created_groups.get(
            next((k for k, v in self._created_groups.items() if v.get("id") == group_id), None)
        )
        return group.get("name") if group else None

    async def _execute_create_channel(self, action: Action, stream_ctx: StreamContext,
                                       exec_ctx: ExecutionContext, template_ctx: dict,
                                       rule_target_group_id: int = None,
                                       normalize_names: bool = False) -> ActionResult:
        """Execute create_channel action."""
        params = action.params
        name_template = params.get("name_template", "{stream_name}")
        channel_name = TemplateVariables.expand_template(name_template, template_ctx, exec_ctx.custom_variables)
        logger.debug("[AUTO-CREATE-EXEC] Template '%s' expanded to '%s'", name_template, channel_name)
        channel_name = self._apply_name_transform(channel_name, params)

        # Track details for the execution log
        action_details = []

        # Apply normalization engine if enabled
        pre_norm_name = channel_name
        if normalize_names and self._normalization_engine:
            try:
                norm_result = self._normalization_engine.normalize(channel_name)
                if norm_result.normalized != channel_name:
                    logger.debug("[AUTO-CREATE-EXEC] Normalized channel name: '%s' -> '%s'", channel_name, norm_result.normalized)
                    action_details.append(f"Name normalized: '{channel_name}' \u2192 '{norm_result.normalized}'")
                    channel_name = norm_result.normalized
            except Exception as e:
                logger.warning("[AUTO-CREATE-EXEC] Failed to normalize channel name '%s': %s", channel_name, e)

        if_exists = params.get("if_exists", "skip")
        group_id = params.get("group_id") or exec_ctx.current_group_id or rule_target_group_id
        logger.debug(
            "[AUTO-CREATE-EXEC] name='%s' if_exists=%s "
            "group_id=%s (param=%s, "
            "exec_ctx=%s, rule=%s)",
            channel_name, if_exists,
            group_id, params.get('group_id'),
            exec_ctx.current_group_id, rule_target_group_id
        )

        # Check if channel already exists (check with original name before number prefix)
        existing = self._find_channel_by_name(channel_name)
        logger.debug("[AUTO-CREATE-EXEC] Lookup '%s': %s", channel_name, 'found id=' + str(existing['id']) if existing else 'not found')

        if existing:
            existing_group_name = self._get_group_name(existing.get("channel_group_id"))
            if existing_group_name:
                action_details.append(f"Existing channel found in group '{existing_group_name}'")

            # When normalization collapsed a distinct name into an existing one,
            # explain how to keep them separate
            normalized_into_existing = (pre_norm_name != channel_name)
            if normalized_into_existing:
                action_details.append(
                    f"'{pre_norm_name}' became '{channel_name}' after normalization and matched an existing channel. "
                    f"To create separate channels instead: use separate rules with different target groups "
                    f"(e.g., one per country), or adjust the name template to keep the distinguishing text."
                )

            if if_exists == "skip":
                exec_ctx.current_channel_id = existing["id"]
                return ActionResult(
                    success=True,
                    action_type=action.type,
                    description=f"Channel '{channel_name}' already exists, skipped",
                    entity_type="channel",
                    entity_id=existing["id"],
                    entity_name=channel_name,
                    skipped=True,
                    details=action_details
                )
            elif if_exists in ("merge", "merge_only"):
                # Add stream to existing channel
                result = await self._add_stream_to_channel(existing, stream_ctx, exec_ctx)
                result.details = action_details + result.details
                return result
            elif if_exists == "update":
                # Update existing channel properties
                return await self._update_channel(existing, stream_ctx, exec_ctx, params)

        # merge_only: don't create new channels, only merge into existing ones
        if if_exists == "merge_only":
            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Channel '{channel_name}' not found, skipped (merge only)",
                entity_type="channel",
                entity_name=channel_name,
                skipped=True,
                details=action_details
            )

        # Determine channel number first (needed for name prefix)
        channel_number = self._get_next_channel_number(params.get("channel_number", "auto"))
        logger.debug("[AUTO-CREATE-EXEC] Channel number: spec=%s -> %s", params.get('channel_number', 'auto'), channel_number)

        # Apply channel number in name if setting is enabled
        base_name = channel_name  # Save before prefix for base-name mapping
        channel_name = self._apply_channel_number_in_name(channel_name, channel_number)
        if channel_name != base_name:
            logger.debug("[AUTO-CREATE-EXEC] Name with number prefix: '%s' -> '%s'", base_name, channel_name)

        # Resolve group name for descriptions
        group_name = self._get_group_name(group_id)
        group_label = f"'{group_name}'" if group_name else str(group_id)

        # Create new channel
        if exec_ctx.dry_run:
            # Track simulated channel so subsequent streams in this run
            # see it as existing (matches execute-mode behavior)
            dry_id = self._next_dry_run_id
            self._next_dry_run_id -= 1
            simulated = {"id": dry_id, "name": channel_name, "channel_number": channel_number,
                         "channel_group_id": group_id, "streams": [stream_ctx.stream_id]}
            if stream_ctx.tvg_id:
                simulated["tvg_id"] = stream_ctx.tvg_id
            self._created_channels[channel_name.lower()] = simulated
            self._channel_by_id[dry_id] = simulated
            # Map base name to prefixed channel so subsequent lookups by base name merge correctly
            if base_name.lower() != channel_name.lower():
                self._base_name_to_channel[base_name.lower()] = simulated
            self._used_channel_numbers.add(channel_number)
            exec_ctx.current_channel_id = dry_id
            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Would create channel '{channel_name}' (#{channel_number}) in group {group_label}",
                entity_type="channel",
                entity_name=channel_name,
                created=True,
                details=action_details
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
            self._channel_by_id[new_channel["id"]] = new_channel
            # Map base name to prefixed channel so subsequent lookups by base name merge correctly
            if base_name.lower() != channel_name.lower():
                self._base_name_to_channel[base_name.lower()] = new_channel
            self._used_channel_numbers.add(channel_number)
            self._channel_assigned_numbers[new_channel["id"]] = channel_number
            exec_ctx.current_channel_id = new_channel["id"]

            # Assign default channel profiles if configured
            profile_desc = await self._assign_default_profiles(new_channel["id"])

            desc = f"Created channel '{channel_name}' (#{channel_number}) in group {group_label}"
            if profile_desc:
                desc += f", {profile_desc}"

            return ActionResult(
                success=True,
                action_type=action.type,
                description=desc,
                entity_type="channel",
                entity_id=new_channel["id"],
                entity_name=channel_name,
                created=True,
                details=action_details
            )

        except Exception as e:
            logger.error("[AUTO-CREATE-EXEC] Failed to create channel '%s': %s", channel_name, e)
            return ActionResult(
                success=False,
                action_type=action.type,
                description=f"Failed to create channel '{channel_name}'",
                error=str(e)
            )

    async def _ensure_channel_m3u_counts(self, channel_id: int) -> None:
        """Lazily fetch and seed per-provider stream counts for a channel."""
        if channel_id in self._seeded_channels:
            return
        self._seeded_channels.add(channel_id)
        try:
            streams = await self.client.get_channel_streams(channel_id)
            for s in streams:
                if isinstance(s, dict) and s.get("m3u_account") is not None:
                    key = (channel_id, s["m3u_account"])
                    self._channel_m3u_counts[key] = self._channel_m3u_counts.get(key, 0) + 1
            logger.debug(
                "[AUTO-CREATE-EXEC] Seeded provider counts for channel %s: "
                "%s providers, %s streams",
                channel_id,
                sum(1 for k in self._channel_m3u_counts if k[0] == channel_id),
                sum(v for k, v in self._channel_m3u_counts.items() if k[0] == channel_id)
            )
        except Exception as e:
            logger.debug("[AUTO-CREATE-EXEC] Failed to fetch streams for channel %s: %s", channel_id, e)

    async def _add_stream_to_channel(self, channel: dict, stream_ctx: StreamContext,
                                      exec_ctx: ExecutionContext) -> ActionResult:
        """Add a stream to an existing channel (merge behavior)."""
        channel_id = channel["id"]
        channel_name = channel["name"]

        # Get current streams
        current_streams = [s["id"] if isinstance(s, dict) else s for s in channel.get("streams", [])]
        logger.debug(
            "[AUTO-CREATE-EXEC] Adding stream %s (%r) "
            "to channel '%s' (id=%s), current streams=%s",
            stream_ctx.stream_id, stream_ctx.stream_name,
            channel_name, channel_id, current_streams
        )

        stream_count = len(current_streams)

        if stream_ctx.stream_id in current_streams:
            exec_ctx.current_channel_id = channel_id
            return ActionResult(
                success=True,
                action_type="merge_stream",
                description=f"Stream already in channel '{channel_name}' ({stream_count} streams)",
                entity_type="channel",
                entity_id=channel_id,
                entity_name=channel_name,
                skipped=True
            )

        new_count = stream_count + 1

        def _track_m3u_count():
            """Increment per-provider stream count for max_streams_per_channel tracking."""
            if stream_ctx.m3u_account_id is not None:
                key = (channel_id, stream_ctx.m3u_account_id)
                prev = self._channel_m3u_counts.get(key, 0)
                self._channel_m3u_counts[key] = prev + 1
                logger.debug(
                    "[AUTO-CREATE-EXEC] Provider stream count for channel '%s' "
                    "(id=%s), provider %s "
                    "(id=%s): %s -> %s",
                    channel_name, channel_id, stream_ctx.m3u_account_name,
                    stream_ctx.m3u_account_id, prev, prev + 1
                )

        if exec_ctx.dry_run:
            # Update cached channel so subsequent dry-run merges see this stream
            channel["streams"] = current_streams + [stream_ctx.stream_id]
            _track_m3u_count()
            exec_ctx.current_channel_id = channel_id
            return ActionResult(
                success=True,
                action_type="merge_stream",
                description=f"Would add stream to channel '{channel_name}' (stream {new_count})",
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
            _track_m3u_count()
            exec_ctx.current_channel_id = channel_id

            return ActionResult(
                success=True,
                action_type="merge_stream",
                description=f"Added stream to channel '{channel_name}' (stream {new_count})",
                entity_type="channel",
                entity_id=channel_id,
                entity_name=channel_name,
                modified=True,
                previous_state=previous_state
            )

        except Exception as e:
            logger.error("[AUTO-CREATE-EXEC] Failed to add stream to channel '%s': %s", channel_name, e)
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

        exec_ctx.current_channel_id = channel_id

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
            logger.error("[AUTO-CREATE-EXEC] Failed to update channel '%s': %s", channel_name, e)
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
        logger.debug("[AUTO-CREATE-EXEC] Template '%s' expanded to '%s'", name_template, group_name)
        group_name = self._apply_name_transform(group_name, params)
        if_exists = params.get("if_exists", "use_existing")
        logger.debug("[AUTO-CREATE-EXEC] name='%s' if_exists=%s", group_name, if_exists)

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
            logger.error("[AUTO-CREATE-EXEC] Failed to create group '%s': %s", group_name, e)
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
                                      exec_ctx: ExecutionContext, template_ctx: dict,
                                      normalize_names: bool = False) -> ActionResult:
        """Execute merge_streams action."""
        params = action.params
        target = params.get("target", "auto")
        find_channel_by = params.get("find_channel_by")
        max_streams = params.get("max_streams_per_channel", 0)  # 0 = unlimited
        find_channel_value = params.get("find_channel_value")
        logger.debug(
            "[AUTO-CREATE-EXEC] target=%s find_by=%s "
            "find_value=%s stream=%r",
            target, find_channel_by,
            find_channel_value, stream_ctx.stream_name
        )

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

            # Auto-fallback: if no find_channel_by was specified and target is "auto",
            # try to find by normalized stream name (strips prefixes, applies normalization)
            if not channel and target == "auto" and not find_channel_by:
                lookup_name = stream_ctx.normalized_name or stream_ctx.stream_name
                # Also try running normalization engine if available
                if self._normalization_engine and not stream_ctx.normalized_name:
                    try:
                        norm_result = self._normalization_engine.normalize(stream_ctx.stream_name)
                        if norm_result.normalized:
                            lookup_name = norm_result.normalized
                    except Exception as e:
                        logger.warning("[AUTO-CREATE-EXEC] Normalization failed for stream '%s': %s", stream_ctx.stream_name, e)
                logger.debug("[AUTO-CREATE-EXEC] Auto-lookup by normalized name: '%s'", lookup_name)
                channel = self._find_channel_by_name(lookup_name)

            # Core-name fallback: strip country prefix + quality suffix using
            # tag groups directly (works even when normalization rules are disabled)
            if not channel and normalize_names and self._normalization_engine:
                try:
                    core_name = self._normalization_engine.extract_core_name(stream_ctx.stream_name)
                    if core_name:
                        logger.debug("[AUTO-CREATE-EXEC] Core name fallback: '%s' -> '%s'", stream_ctx.stream_name, core_name)
                        channel = self._core_name_to_channel.get(core_name.lower()) or self._find_channel_by_name(core_name)

                        # Sub-step A: Deparenthesize stream core name and retry
                        if not channel:
                            deparen = re.sub(r'\(([^)]+)\)', r'\1', core_name)
                            deparen = re.sub(r'\s+', ' ', deparen).strip()
                            if deparen.lower() != core_name.lower():
                                logger.debug("[AUTO-CREATE-EXEC] Deparen fallback: '%s' -> '%s'", core_name, deparen)
                                channel = self._core_name_to_channel.get(deparen.lower()) \
                                          or self._find_channel_by_name(deparen)

                        # Sub-step B: Word-prefix containment (single-candidate only)
                        if not channel:
                            lookup = re.sub(r'\(([^)]+)\)', r'\1', core_name).lower()
                            lookup = re.sub(r'\s+', ' ', lookup).strip()
                            lookup_words = lookup.split()
                            if len(lookup_words) >= 2:
                                candidates = []
                                for ch_core, ch_val in self._core_name_to_channel.items():
                                    ch_words = ch_core.split()
                                    if len(ch_words) >= 2:
                                        shorter, longer = (lookup_words, ch_words) \
                                            if len(lookup_words) <= len(ch_words) \
                                            else (ch_words, lookup_words)
                                        if longer[:len(shorter)] == shorter:
                                            candidates.append(ch_val)
                                if len(candidates) == 1:
                                    channel = candidates[0]
                                    logger.debug("[AUTO-CREATE-EXEC] Word-prefix matched '%s' (id=%s)", channel.get('name'), channel.get('id'))
                                elif len(candidates) > 1:
                                    logger.debug("[AUTO-CREATE-EXEC] Word-prefix skipped: %s ambiguous candidates for '%s'", len(candidates), core_name)

                        if channel:
                            logger.debug("[AUTO-CREATE-EXEC] Core name matched '%s' (id=%s)", channel.get('name'), channel.get('id'))
                except Exception as e:
                    logger.debug("[AUTO-CREATE-EXEC] Core name fallback failed: %s", e)

            # Call-sign fallback: match local affiliates by FCC call sign
            # (W/K + 2-3 letters) extracted from both stream and channel names
            if not channel and normalize_names and self._normalization_engine:
                try:
                    cs = self._normalization_engine.extract_call_sign(stream_ctx.stream_name)
                    if cs:
                        logger.debug("[AUTO-CREATE-EXEC] Call sign fallback: '%s' -> '%s'", stream_ctx.stream_name, cs)
                        channel = self._callsign_to_channel.get(cs)
                        if channel:
                            logger.debug("[AUTO-CREATE-EXEC] Call sign matched '%s' (id=%s)", channel.get('name'), channel.get('id'))
                except Exception as e:
                    logger.debug("[AUTO-CREATE-EXEC] Call sign fallback failed: %s", e)

            if channel:
                # Enforce per-provider stream limit if configured
                if max_streams > 0 and stream_ctx.m3u_account_id is not None:
                    await self._ensure_channel_m3u_counts(channel["id"])
                    provider_name = stream_ctx.m3u_account_name or f"provider #{stream_ctx.m3u_account_id}"
                    key = (channel["id"], stream_ctx.m3u_account_id)
                    current_count = self._channel_m3u_counts.get(key, 0)
                    logger.debug(
                        "[AUTO-CREATE-EXEC] Max streams check: channel '%s' has "
                        "%s/%s stream(s) from %s",
                        channel['name'], current_count, max_streams, provider_name
                    )
                    if current_count >= max_streams:
                        logger.info(
                            "[AUTO-CREATE-EXEC] Skipped stream '%s': "
                            "channel '%s' already has %s stream(s) "
                            "from %s (limit: %s)",
                            stream_ctx.stream_name, channel['name'],
                            current_count, provider_name, max_streams
                        )
                        return ActionResult(
                            success=True, action_type=action.type,
                            description=f"Skipped: '{channel['name']}' already has "
                                        f"{current_count} stream(s) from {provider_name} "
                                        f"(limit: {max_streams}/provider)",
                            entity_type="channel", entity_id=channel["id"],
                            entity_name=channel["name"], skipped=True
                        )
                return await self._add_stream_to_channel(channel, stream_ctx, exec_ctx)
            elif target == "existing_channel":
                return ActionResult(
                    success=False,
                    action_type=action.type,
                    description=f"No channel found matching {find_channel_by}='{find_channel_value}'",
                    error="Channel not found for merge"
                )
            # For auto target, no matching channel found — skip
            # merge_streams only adds streams to existing channels;
            # use a create_channel action if new channels are needed.

        return ActionResult(
            success=True,
            action_type=action.type,
            description="No existing channel found — stream skipped (merge_streams only adds to existing channels)",
            skipped=True
        )

    # =========================================================================
    # Property Assignment Actions
    # =========================================================================

    async def _execute_assign_logo(self, action: Action, stream_ctx: StreamContext,
                                    exec_ctx: ExecutionContext,
                                    template_ctx: dict) -> ActionResult:
        """Execute assign_logo action."""
        if not exec_ctx.current_channel_id:
            return ActionResult(
                success=False,
                action_type=action.type,
                description="No channel context for assign_logo",
                error="No channel to update"
            )

        value = action.params.get("value", "from_stream")
        if value == "from_stream":
            logo_url = stream_ctx.logo_url
        else:
            logo_url = TemplateVariables.expand_template(value, template_ctx, exec_ctx.custom_variables)

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
                                      exec_ctx: ExecutionContext,
                                      template_ctx: dict) -> ActionResult:
        """Execute assign_tvg_id action."""
        if not exec_ctx.current_channel_id:
            return ActionResult(
                success=False,
                action_type=action.type,
                description="No channel context for assign_tvg_id",
                error="No channel to update"
            )

        value = action.params.get("value", "from_stream")
        if value == "from_stream":
            tvg_id = stream_ctx.tvg_id
        else:
            tvg_id = TemplateVariables.expand_template(value, template_ctx, exec_ctx.custom_variables)

        if not tvg_id:
            return ActionResult(
                success=True,
                action_type=action.type,
                description="No tvg_id to assign",
                skipped=True
            )

        if exec_ctx.dry_run:
            # Update simulated channel so subsequent actions (e.g. assign_epg) can use the tvg_id
            simulated = self._channel_by_id.get(exec_ctx.current_channel_id)
            if simulated is not None:
                simulated["tvg_id"] = tvg_id
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
        """Execute assign_epg action.

        The user selects an EPG source ID (epg_id), but Dispatcharr channels use
        epg_data_id (an EPG data entry). This method resolves the source to the
        best-matching data entry:
        1. For dummy EPGs (1 entry per source): uses that single entry
        2. For standard EPGs: matches by the channel's tvg_id
        3. Fallback: first entry from the source
        """
        if not exec_ctx.current_channel_id:
            return ActionResult(
                success=False,
                action_type=action.type,
                description="No channel context for assign_epg",
                error="No channel to update"
            )

        epg_source_id = action.params.get("epg_id")
        if epg_source_id is None:
            return ActionResult(
                success=False,
                action_type=action.type,
                description="No epg_id specified",
                error="Missing epg_id"
            )

        # Resolve EPG source ID -> epg_data_id
        source_entries = self._epg_data_by_source.get(epg_source_id, [])
        if not source_entries:
            # For dummy EPG sources, defer instead of failing — Pass 5 will refresh and retry
            if epg_source_id in self._dummy_epg_source_ids:
                logger.info(
                    "[AUTO-CREATE-EXEC] Deferring assign_epg for dummy source %s "
                    "(channel %s) — will retry after EPG refresh",
                    epg_source_id, exec_ctx.current_channel_id
                )
                self._deferred_epg_assignments.append(
                    (exec_ctx.current_channel_id, action, stream_ctx, exec_ctx)
                )
                return ActionResult(
                    success=True,
                    action_type=action.type,
                    description=f"Deferred: will assign EPG from dummy source {epg_source_id} after refresh",
                    entity_type="channel",
                    entity_id=exec_ctx.current_channel_id,
                    deferred=True
                )
            logger.warning(
                "[AUTO-CREATE-EXEC] No EPG data entries found for source %s",
                epg_source_id
            )
            return ActionResult(
                success=False,
                action_type=action.type,
                description=f"No EPG data entries found for source {epg_source_id}",
                error=f"EPG source {epg_source_id} has no data entries"
            )

        channel = self._channel_by_id.get(exec_ctx.current_channel_id, {})
        epg_data_entry = self._match_epg_data(channel, source_entries)

        if not epg_data_entry:
            channel_name = channel.get("name", "unknown")
            logger.warning("[AUTO-CREATE-EXEC] No EPG match for channel '%s' in source %s", channel_name, epg_source_id)
            return ActionResult(
                success=False,
                action_type=action.type,
                description=f"No matching EPG data for '{channel_name}' in source {epg_source_id}",
                error="No EPG data match found"
            )

        epg_data_id = epg_data_entry["id"]
        set_tvg_id = action.params.get("set_tvg_id", False)
        epg_tvg_id = epg_data_entry.get("tvg_id")

        if exec_ctx.dry_run:
            desc = f"Would assign EPG data {epg_data_id} (source {epg_source_id}) to channel"
            if set_tvg_id and epg_tvg_id:
                channel["tvg_id"] = epg_tvg_id
                desc += f" and set tvg_id to '{epg_tvg_id}'"
            return ActionResult(
                success=True,
                action_type=action.type,
                description=desc,
                entity_type="channel",
                entity_id=exec_ctx.current_channel_id,
                modified=True
            )

        try:
            previous_state = {"epg_data_id": channel.get("epg_data_id")}
            payload = {"epg_data_id": epg_data_id}
            if set_tvg_id and epg_tvg_id:
                previous_state["tvg_id"] = channel.get("tvg_id")
                payload["tvg_id"] = epg_tvg_id

            await self.client.update_channel(exec_ctx.current_channel_id, payload)

            logger.debug(
                "[AUTO-CREATE-EXEC] Assigned epg_data_id=%s (source=%s, "
                "tvg_id=%s) to channel %s",
                epg_data_id, epg_source_id,
                epg_data_entry.get('tvg_id'), exec_ctx.current_channel_id
            )

            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Assigned EPG data {epg_data_id} (source {epg_source_id}) to channel",
                entity_type="channel",
                entity_id=exec_ctx.current_channel_id,
                modified=True,
                previous_state=previous_state
            )
        except Exception as e:
            logger.error("[AUTO-CREATE-EXEC] Failed to assign EPG: %s", e)
            return ActionResult(
                success=False,
                action_type=action.type,
                description="Failed to assign EPG",
                error=str(e)
            )

    def reload_epg_data(self, epg_data: list):
        """Rebuild EPG data lookup from fresh data (called by engine in Pass 5)."""
        self._epg_data_by_source.clear()
        for entry in (epg_data or []):
            src_id = entry.get("epg_source")
            if src_id is not None:
                self._epg_data_by_source.setdefault(src_id, []).append(entry)
        logger.info(
            "[AUTO-CREATE-EXEC] Reloaded EPG data: %s sources, %s total entries",
            len(self._epg_data_by_source), len(epg_data or [])
        )

    # =========================================================================
    # EPG Matching (mirrors frontend epgMatching.ts logic)
    # =========================================================================

    # Quality/timezone suffixes stripped during normalization
    _QUALITY_SUFFIXES = ['fhd', 'uhd', '4k', 'hd', 'sd', '1080p', '1080i', '720p', '480p', '2160p', 'hevc', 'h264', 'h265']
    _TIMEZONE_SUFFIXES = ['east', 'west', 'et', 'pt', 'ct', 'mt']
    _LEAGUE_SUFFIXES = ['nfl', 'nba', 'mlb', 'nhl', 'mls', 'wnba', 'ncaa', 'cfb', 'cbb',
                        'epl', 'premierleague', 'laliga', 'bundesliga', 'seriea', 'ligue1',
                        'uefa', 'fifa', 'f1', 'nascar', 'pga', 'atp', 'wta', 'wwe', 'ufc', 'aew', 'boxing']
    _LEAGUE_PREFIXES_RE = re.compile(
        r'^(?:NFL|NBA|MLB|NHL|MLS|WNBA|NCAA|CFB|CBB|EPL|UEFA|FIFA|F1|NASCAR|PGA|ATP|WTA|WWE|UFC|AEW|BOXING)\s*[:|]\s*',
        re.IGNORECASE
    )

    @staticmethod
    def _normalize_for_epg(name: str) -> str:
        """Normalize a channel/EPG name for matching (mirrors frontend normalizeForEPGMatch)."""
        n = name.strip()
        # Strip channel number prefix: "107 | Name", "107 - Name", "107: Name"
        n = re.sub(r'^\d+(?:\.\d+)?\s*[|\-:.]\s*', '', n)
        # Strip "107 Name" (number + space + letter)
        n = re.sub(r'^\d+(?:\.\d+)?\s+(?=[A-Za-z])', '', n)
        # Strip country prefix: "US: Name", "UK | Name"
        n = re.sub(r'^[A-Z]{2}\s*[:|]\s*', '', n)
        # Strip league prefix: "NFL: Arizona Cardinals"
        n = ActionExecutor._LEAGUE_PREFIXES_RE.sub('', n)
        # Strip quality suffixes
        for suffix in ActionExecutor._QUALITY_SUFFIXES:
            n = re.sub(rf'[\s\-_|:]*{suffix}\s*$', '', n, flags=re.IGNORECASE)
        # Strip timezone suffixes
        for suffix in ActionExecutor._TIMEZONE_SUFFIXES:
            n = re.sub(rf'[\s\-_|:]*{suffix}\s*$', '', n, flags=re.IGNORECASE)
        # Convert semantic characters
        n = n.replace('+', 'plus').replace('&', 'and')
        # Lowercase alphanumeric only
        n = re.sub(r'[^a-z0-9]', '', n.lower())
        # Strip leading digits
        n = re.sub(r'^\d+', '', n)
        return n

    @staticmethod
    def _parse_tvg_id(tvg_id: str) -> str:
        """Parse tvg_id to extract the normalized base name (mirrors frontend parseTvgId)."""
        lower = tvg_id.lower()
        last_dot = lower.rfind('.')
        name_part = tvg_id

        if last_dot != -1:
            suffix = lower[last_dot + 1:]
            # Known league suffix
            if suffix in ActionExecutor._LEAGUE_SUFFIXES:
                name_part = tvg_id[:last_dot]
            # Looks like a country code (2-3 lowercase letters)
            elif 2 <= len(suffix) <= 3 and suffix.isalpha():
                name_part = tvg_id[:last_dot]

        # Strip call signs in parentheses: "AdultSwim(ADSM)" -> "AdultSwim"
        name_part = re.sub(r'\([^)]+\)', '', name_part)
        return ActionExecutor._normalize_for_epg(name_part)

    def _match_epg_data(self, channel: dict, source_entries: list[dict]) -> Optional[dict]:
        """
        Find the best EPG data entry for a channel from a list of source entries.
        Mirrors the frontend's "Accept Best Guesses" matching logic.

        Match priority:
        1. Exact tvg_id match (channel.tvg_id == entry.tvg_id)
        2. Exact normalized name match (channel name == entry tvg_id or name)
        3. Prefix match (channel name starts with entry name or vice versa)
        4. Fallback: first entry (for single-entry sources like dummy EPGs)
        """
        channel_tvg_id = channel.get("tvg_id")
        channel_name = channel.get("name", "")

        # 1. Exact tvg_id match
        if channel_tvg_id:
            for entry in source_entries:
                if entry.get("tvg_id") == channel_tvg_id:
                    logger.debug("[AUTO-CREATE-EXEC] Exact tvg_id match: %s", channel_tvg_id)
                    return entry

        # Normalize channel name
        norm_channel = self._normalize_for_epg(channel_name)
        if not norm_channel:
            # Can't match by name, use fallback
            if len(source_entries) == 1:
                logger.debug("[AUTO-CREATE-EXEC] Single entry fallback for '%s'", channel_name)
                return source_entries[0]
            return None

        # Build lookup from source entries
        exact_matches = []
        prefix_matches = []

        for entry in source_entries:
            entry_tvg_id = entry.get("tvg_id") or ""
            entry_name = entry.get("name") or ""

            # Normalize tvg_id and name
            norm_tvg = self._parse_tvg_id(entry_tvg_id) if entry_tvg_id else ""
            norm_name = self._normalize_for_epg(entry_name) if entry_name else ""

            # 2. Exact normalized match
            if norm_channel == norm_tvg or norm_channel == norm_name:
                exact_matches.append((entry, norm_tvg, abs(len(norm_tvg) - len(norm_channel))))
                continue

            # Also check call sign in parentheses: "CartoonNetwork(STOONHD).us"
            call_sign_match = re.search(r'\(([^)]+)\)', entry_tvg_id)
            if call_sign_match:
                call_sign = re.sub(r'[^a-z0-9]', '', call_sign_match.group(1).lower())
                # Strip HD/SD suffix from call sign
                call_sign_base = re.sub(r'(hd|sd|fhd|uhd)$', '', call_sign)
                if norm_channel == call_sign or norm_channel == call_sign_base:
                    exact_matches.append((entry, norm_tvg, 0))
                    continue

            # 3. Prefix match (at least 4 chars to avoid false positives)
            if len(norm_channel) >= 4 and norm_tvg:
                if norm_tvg.startswith(norm_channel) or norm_channel.startswith(norm_tvg):
                    len_diff = abs(len(norm_tvg) - len(norm_channel))
                    prefix_matches.append((entry, norm_tvg, len_diff))
            if len(norm_channel) >= 4 and norm_name:
                if norm_name.startswith(norm_channel) or norm_channel.startswith(norm_name):
                    len_diff = abs(len(norm_name) - len(norm_channel))
                    # Avoid duplicates
                    if not any(e[0]["id"] == entry["id"] for e in prefix_matches):
                        prefix_matches.append((entry, norm_name, len_diff))

        # Pick best match: exact > prefix, then sort by name length similarity
        if exact_matches:
            exact_matches.sort(key=lambda x: x[2])
            best = exact_matches[0][0]
            logger.debug(
                "[AUTO-CREATE-EXEC] Exact name match: '%s' -> "
                "'%s' (tvg_id=%s)",
                channel_name, best.get('name'), best.get('tvg_id')
            )
            return best

        if prefix_matches:
            prefix_matches.sort(key=lambda x: x[2])
            best = prefix_matches[0][0]
            logger.debug(
                "[AUTO-CREATE-EXEC] Prefix match: '%s' -> "
                "'%s' (tvg_id=%s)",
                channel_name, best.get('name'), best.get('tvg_id')
            )
            return best

        # 4. Fallback for single-entry sources (dummy EPGs)
        if len(source_entries) == 1:
            logger.debug("[AUTO-CREATE-EXEC] Single entry fallback for '%s'", channel_name)
            return source_entries[0]

        return None

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

        # Reuse the number if this channel was already assigned one this run
        # (avoids consuming extra numbers when multiple streams merge into same channel)
        existing_number = self._channel_assigned_numbers.get(exec_ctx.current_channel_id)
        if existing_number is not None:
            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Channel already numbered #{existing_number} this run",
                entity_type="channel",
                entity_id=exec_ctx.current_channel_id,
                skipped=True
            )

        channel_number = self._get_next_channel_number(value)

        if exec_ctx.dry_run:
            self._channel_assigned_numbers[exec_ctx.current_channel_id] = channel_number
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
            self._channel_assigned_numbers[exec_ctx.current_channel_id] = channel_number

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
        logger.debug("[AUTO-CREATE-EXEC] var_name='%s' mode=%s params=%s", var_name, mode, params)

        # Get source value for regex modes
        source_value = ""
        if mode in ("regex_extract", "regex_replace"):
            source_field = params.get("source_field", "stream_name")
            source_value = template_ctx.get(source_field, "")
            logger.debug("[AUTO-CREATE-EXEC] source_field=%s source_value=%r", source_field, source_value)

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
    # Stream Management Actions
    # =========================================================================

    async def _execute_remove_from_channel(self, action: Action, stream_ctx: StreamContext,
                                            exec_ctx: ExecutionContext) -> ActionResult:
        """Execute remove_from_channel action — unassign a stream from its current channel."""
        if not stream_ctx.channel_id:
            return ActionResult(
                success=True,
                action_type=action.type,
                description="Stream not assigned to any channel, skipped",
                skipped=True
            )

        channel_id = stream_ctx.channel_id
        channel = self._channel_by_id.get(channel_id)
        if not channel:
            try:
                channel = await self.client.get_channel(channel_id)
            except Exception as e:
                return ActionResult(
                    success=False,
                    action_type=action.type,
                    description=f"Failed to fetch channel {channel_id}",
                    error=str(e)
                )

        channel_name = channel.get("name", f"ID:{channel_id}")

        # Get current stream list
        current_streams = [s["id"] if isinstance(s, dict) else s for s in channel.get("streams", [])]
        if stream_ctx.stream_id not in current_streams:
            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Stream not in channel '{channel_name}', skipped",
                skipped=True
            )

        filtered_streams = [s for s in current_streams if s != stream_ctx.stream_id]

        if exec_ctx.dry_run:
            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Would remove stream from channel '{channel_name}'",
                entity_type="stream",
                entity_id=stream_ctx.stream_id,
                entity_name=stream_ctx.stream_name,
                modified=True
            )

        try:
            previous_state = {"streams": current_streams.copy()}
            await self.client.update_channel(channel_id, {"streams": filtered_streams})
            channel["streams"] = filtered_streams

            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Removed stream from channel '{channel_name}'",
                entity_type="stream",
                entity_id=stream_ctx.stream_id,
                entity_name=stream_ctx.stream_name,
                modified=True,
                previous_state=previous_state
            )
        except Exception as e:
            logger.error("[AUTO-CREATE-EXEC] Failed to remove stream from channel '%s': %s", channel_name, e)
            return ActionResult(
                success=False,
                action_type=action.type,
                description=f"Failed to remove stream from channel '{channel_name}'",
                error=str(e)
            )

    async def _execute_set_stream_priority(self, action: Action, stream_ctx: StreamContext,
                                            exec_ctx: ExecutionContext) -> ActionResult:
        """Execute set_stream_priority action — move stream to lowest or highest position."""
        if not stream_ctx.channel_id:
            return ActionResult(
                success=True,
                action_type=action.type,
                description="Stream not assigned to any channel, skipped",
                skipped=True
            )

        priority = action.params.get("priority", "lowest")
        channel_id = stream_ctx.channel_id
        channel = self._channel_by_id.get(channel_id)
        if not channel:
            try:
                channel = await self.client.get_channel(channel_id)
            except Exception as e:
                return ActionResult(
                    success=False,
                    action_type=action.type,
                    description=f"Failed to fetch channel {channel_id}",
                    error=str(e)
                )

        channel_name = channel.get("name", f"ID:{channel_id}")

        current_streams = [s["id"] if isinstance(s, dict) else s for s in channel.get("streams", [])]
        if stream_ctx.stream_id not in current_streams:
            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Stream not in channel '{channel_name}', skipped",
                skipped=True
            )

        # Build reordered list
        without = [s for s in current_streams if s != stream_ctx.stream_id]
        if priority == "highest":
            reordered = [stream_ctx.stream_id] + without
        else:
            reordered = without + [stream_ctx.stream_id]

        if reordered == current_streams:
            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Stream already at {priority} priority in '{channel_name}', skipped",
                skipped=True
            )

        if exec_ctx.dry_run:
            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Would move stream to {priority} priority in channel '{channel_name}'",
                entity_type="channel",
                entity_id=channel_id,
                entity_name=channel_name,
                modified=True
            )

        try:
            previous_state = {"streams": current_streams.copy()}
            await self.client.update_channel(channel_id, {"streams": reordered})
            channel["streams"] = reordered

            return ActionResult(
                success=True,
                action_type=action.type,
                description=f"Moved stream to {priority} priority in channel '{channel_name}'",
                entity_type="channel",
                entity_id=channel_id,
                entity_name=channel_name,
                modified=True,
                previous_state=previous_state
            )
        except Exception as e:
            logger.error("[AUTO-CREATE-EXEC] Failed to set stream priority in '%s': %s", channel_name, e)
            return ActionResult(
                success=False,
                action_type=action.type,
                description=f"Failed to set stream priority in channel '{channel_name}'",
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
            logger.info("[AUTO-CREATE-EXEC] Deleted orphaned channel %s (%s)", channel_id, channel_name)

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
                logger.info("[AUTO-CREATE-EXEC] Channel %s already deleted (404)", channel_id)
                return ActionResult(
                    success=True,
                    action_type="remove_channel",
                    description=f"Channel {channel_id} already deleted",
                    entity_type="channel",
                    entity_id=channel_id,
                )
            logger.error("[AUTO-CREATE-EXEC] Failed to delete channel %s: %s", channel_id, e)
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
            logger.info("[AUTO-CREATE-EXEC] Moved orphaned channel %s (%s) to Uncategorized", channel_id, channel_name)

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
                logger.info("[AUTO-CREATE-EXEC] Channel %s already deleted (404)", channel_id)
                return ActionResult(
                    success=True,
                    action_type="move_channel",
                    description=f"Channel {channel_id} already deleted",
                    entity_type="channel",
                    entity_id=channel_id,
                )
            logger.error("[AUTO-CREATE-EXEC] Failed to move channel %s: %s", channel_id, e)
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
            logger.info("[AUTO-CREATE-EXEC] Deleted empty group %s (%s)", group_id, group_name)

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
            logger.error("[AUTO-CREATE-EXEC] Failed to delete group %s: %s", group_id, e)
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
            logger.debug("[AUTO-CREATE-EXEC] '%s' -> '%s'", channel_name, result)
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
                logger.warning("[AUTO-CREATE-EXEC] Failed to update profile %s for channel %s: %s", pid, channel_id, e)

        if enabled_count or disabled_count:
            desc = f"profiles: enabled in {enabled_count}, disabled in {disabled_count}"
            logger.info("[AUTO-CREATE-EXEC] Channel %s: %s", channel_id, desc)
            return desc
        return ""

    def _find_channel_by_name(self, name: str) -> Optional[dict]:
        """Find channel by exact name (case-insensitive).

        Also checks the base-name mapping so that a lookup for "USA Network"
        finds a channel created as "4000 | USA Network", and the normalized-name
        mapping so that merge_streams can find channels the same way
        normalized_name_in_group does.
        """
        name_lower = name.lower()
        # Check newly created channels first (by exact name)
        if name_lower in self._created_channels:
            logger.debug("[AUTO-CREATE-EXEC] '%s' found in created channels", name)
            return self._created_channels[name_lower]
        # Check base-name mapping (base name -> number-prefixed channel)
        if name_lower in self._base_name_to_channel:
            logger.debug("[AUTO-CREATE-EXEC] '%s' found via base-name mapping", name)
            return self._base_name_to_channel[name_lower]
        result = self._channel_by_name.get(name_lower)
        if result:
            logger.debug("[AUTO-CREATE-EXEC] '%s' found in existing channels (id=%s)", name, result.get('id'))
            return result
        # Check normalized-name mapping (normalization-engine-processed channel names)
        if name_lower in self._normalized_name_to_channel:
            result = self._normalized_name_to_channel[name_lower]
            logger.debug("[AUTO-CREATE-EXEC] '%s' found via normalized-name mapping (id=%s, name='%s')", name, result.get('id'), result.get('name'))
            return result
        return None

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
            logger.debug("[AUTO-CREATE-EXEC] Invalid regex in channel name pattern")
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
            num = spec
            while num in self._used_channel_numbers:
                num += 1
            logger.debug("[AUTO-CREATE-EXEC] spec=%s (int) -> %s", spec, num)
            return num

        if isinstance(spec, str):
            if spec == "auto":
                # Find next available number starting from 1
                num = 1
                while num in self._used_channel_numbers:
                    num += 1
                logger.debug("[AUTO-CREATE-EXEC] spec='auto' -> %s (skipped %s used numbers)", num, num - 1)
                return num

            # Check for range format "min-max"
            match = re.match(r"^(\d+)-(\d+)$", spec)
            if match:
                min_num = int(match.group(1))
                max_num = int(match.group(2))
                for num in range(min_num, max_num + 1):
                    if num not in self._used_channel_numbers:
                        logger.debug("[AUTO-CREATE-EXEC] spec='%s' (range) -> %s", spec, num)
                        return num
                # Range exhausted, use next after max
                logger.debug("[AUTO-CREATE-EXEC] spec='%s' range exhausted -> %s", spec, max_num + 1)
                return max_num + 1

            # Try parsing as int — auto-increment from this starting number
            try:
                num = int(spec)
                while num in self._used_channel_numbers:
                    num += 1
                logger.debug("[AUTO-CREATE-EXEC] spec='%s' (parsed int) -> %s", spec, num)
                return num
            except ValueError:
                logger.debug("[AUTO-CREATE-EXEC] Non-numeric channel number spec %r, falling back to auto", spec)

        # Fallback to auto
        num = 1
        while num in self._used_channel_numbers:
            num += 1
        logger.debug("[AUTO-CREATE-EXEC] spec=%r (fallback auto) -> %s", spec, num)
        return num
