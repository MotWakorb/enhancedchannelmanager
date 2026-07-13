"""
Auto-Creation Pipeline Schema Definitions

Defines the structure of conditions and actions used in auto-creation rules.
Includes validation, parsing, and serialization utilities.
"""
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Union
import re
import json

import safe_regex
from regex_lint import lint_replacement_group_refs

logger = logging.getLogger(__name__)


# =============================================================================
# Condition Types
# =============================================================================

class ConditionType(str, Enum):
    """Types of conditions that can be evaluated against streams."""

    # Stream metadata conditions
    STREAM_NAME_MATCHES = "stream_name_matches"      # Regex match on stream name
    STREAM_NAME_CONTAINS = "stream_name_contains"    # Substring match
    STREAM_GROUP_CONTAINS = "stream_group_contains"  # Substring match on group name
    STREAM_GROUP_MATCHES = "stream_group_matches"    # Regex match on group name
    STREAM_GROUP_IS = "stream_group_is"              # Exact match on provider group (M3U group_title)
    TVG_ID_EXISTS = "tvg_id_exists"                  # Has EPG ID
    TVG_ID_MATCHES = "tvg_id_matches"                # Regex match on EPG ID
    LOGO_EXISTS = "logo_exists"                      # Has logo URL
    PROVIDER_IS = "provider_is"                      # From specific M3U account(s)
    QUALITY_MIN = "quality_min"                      # Minimum resolution (height)
    QUALITY_MAX = "quality_max"                      # Maximum resolution (height)
    CODEC_IS = "codec_is"                            # Video codec filter
    HAS_AUDIO_TRACKS = "has_audio_tracks"            # Minimum audio tracks

    # Channel conditions (check existing channels)
    HAS_CHANNEL = "has_channel"                      # Stream already assigned to a channel
    CHANNEL_EXISTS_WITH_NAME = "channel_exists_with_name"  # Exact channel name exists
    CHANNEL_EXISTS_MATCHING = "channel_exists_matching"    # Regex match on existing channels
    CHANNEL_IN_GROUP = "channel_in_group"            # Channel exists in specific group
    CHANNEL_HAS_STREAMS = "channel_has_streams"      # Channel already has N streams
    NORMALIZED_NAME_IN_GROUP = "normalized_name_in_group"  # Normalized stream name matches a channel in group
    NORMALIZED_NAME_NOT_IN_GROUP = "normalized_name_not_in_group"  # Normalized name does NOT match any channel in group
    NORMALIZED_NAME_EXISTS = "normalized_name_exists"  # Normalized name matches a channel in ANY group
    NORMALIZED_NAME_NOT_EXISTS = "normalized_name_not_exists"  # Normalized name does NOT match any channel in any group

    # Logical operators
    AND = "and"
    OR = "or"
    NOT = "not"

    # Special
    ALWAYS = "always"                                # Always matches
    NEVER = "never"                                  # Never matches


@dataclass
class Condition:
    """
    Represents a single condition or compound condition.

    Simple condition:
        Condition(type="stream_name_contains", value="Sports")

    Compound condition:
        Condition(type="and", conditions=[
            Condition(type="stream_name_contains", value="ESPN"),
            Condition(type="quality_min", value=720)
        ])
    """
    type: str
    value: Optional[Any] = None
    conditions: Optional[list] = None  # For AND/OR/NOT operators (legacy)
    connector: str = "and"  # How this condition relates to previous: "and" or "or"
    case_sensitive: bool = False
    negate: bool = False  # Inverts the result

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        result = {"type": self.type}
        if self.value is not None:
            result["value"] = self.value
        if self.conditions:
            result["conditions"] = [c.to_dict() if isinstance(c, Condition) else c for c in self.conditions]
        if self.connector and self.connector != "and":
            result["connector"] = self.connector
        if self.case_sensitive:
            result["case_sensitive"] = True
        if self.negate:
            result["negate"] = True
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "Condition":
        """Create Condition from dictionary."""
        if isinstance(data, Condition):
            return data

        conditions = None
        if "conditions" in data:
            conditions = [cls.from_dict(c) for c in data["conditions"]]

        return cls(
            type=data.get("type", "always"),
            value=data.get("value"),
            conditions=conditions,
            connector=data.get("connector", "and"),
            case_sensitive=data.get("case_sensitive", False),
            negate=data.get("negate", False)
        )

    def validate(self) -> list[str]:
        """
        Validate the condition structure.
        Returns list of error messages (empty if valid).
        """
        errors = []

        try:
            cond_type = ConditionType(self.type)
        except ValueError:
            logger.warning("[AUTO-CREATE-SCHEMA] Unknown condition type: %s", self.type)
            errors.append(f"Unknown condition type: {self.type}")
            return errors

        # Validate logical operators have sub-conditions
        if cond_type in (ConditionType.AND, ConditionType.OR):
            if not self.conditions or len(self.conditions) < 1:
                errors.append(f"{self.type} requires at least 1 sub-condition")
            else:
                for i, sub in enumerate(self.conditions):
                    if isinstance(sub, Condition):
                        sub_errors = sub.validate()
                        errors.extend([f"{self.type}[{i}]: {e}" for e in sub_errors])

        elif cond_type == ConditionType.NOT:
            if not self.conditions or len(self.conditions) != 1:
                errors.append("'not' requires exactly 1 sub-condition")
            elif isinstance(self.conditions[0], Condition):
                errors.extend(self.conditions[0].validate())

        # Validate value requirements
        elif cond_type in (
            ConditionType.STREAM_NAME_MATCHES,
            ConditionType.STREAM_NAME_CONTAINS,
            ConditionType.STREAM_GROUP_CONTAINS,
            ConditionType.STREAM_GROUP_MATCHES,
            ConditionType.STREAM_GROUP_IS,
            ConditionType.TVG_ID_MATCHES,
            ConditionType.CHANNEL_EXISTS_WITH_NAME,
            ConditionType.CHANNEL_EXISTS_MATCHING,
        ):
            if not self.value or not isinstance(self.value, str):
                errors.append(f"{self.type} requires a string value")
            # Validate regex patterns
            if cond_type in (
                ConditionType.STREAM_NAME_MATCHES,
                ConditionType.STREAM_GROUP_MATCHES,
                ConditionType.TVG_ID_MATCHES,
                ConditionType.CHANNEL_EXISTS_MATCHING,
            ):
                # bd-ltjyx: route write-time syntax validation through
                # safe_regex.compile. SafeRegexError covers both
                # PatternTooLongError (length cap) and wrapped regex
                # syntax errors, mirroring the runtime path used by
                # channel_pipeline_evaluator (bd-eio04.15).
                try:
                    safe_regex.compile(self.value)
                except safe_regex.SafeRegexError as e:
                    errors.append(f"Invalid regex pattern for {self.type}: {e}")

        elif cond_type in (ConditionType.QUALITY_MIN, ConditionType.QUALITY_MAX, ConditionType.CHANNEL_HAS_STREAMS, ConditionType.HAS_AUDIO_TRACKS):
            if not isinstance(self.value, (int, float)) or self.value < 0:
                errors.append(f"{self.type} requires a positive number")

        elif cond_type == ConditionType.PROVIDER_IS:
            if not isinstance(self.value, (int, list)):
                errors.append(f"{self.type} requires an integer or list of integers")
            elif isinstance(self.value, list) and not all(isinstance(x, int) for x in self.value):
                errors.append(f"{self.type} list must contain only integers")

        elif cond_type == ConditionType.CODEC_IS:
            if not isinstance(self.value, (str, list)):
                errors.append(f"{self.type} requires a string or list of strings")
            elif isinstance(self.value, list) and not all(isinstance(x, str) for x in self.value):
                errors.append(f"{self.type} list must contain only strings")

        elif cond_type == ConditionType.CHANNEL_IN_GROUP:
            if not isinstance(self.value, int):
                errors.append(f"{self.type} requires a group ID (integer)")

        elif cond_type in (ConditionType.NORMALIZED_NAME_IN_GROUP, ConditionType.NORMALIZED_NAME_NOT_IN_GROUP):
            if not isinstance(self.value, int):
                errors.append(f"{self.type} requires a group ID (integer)")

        elif cond_type in (ConditionType.NORMALIZED_NAME_EXISTS, ConditionType.NORMALIZED_NAME_NOT_EXISTS):
            pass  # No value required — checks all groups

        elif cond_type in (ConditionType.TVG_ID_EXISTS, ConditionType.LOGO_EXISTS, ConditionType.HAS_CHANNEL):
            if self.value is not None and not isinstance(self.value, bool):
                errors.append(f"{self.type} value should be boolean or omitted")

        if errors:
            logger.warning("[AUTO-CREATE-SCHEMA] Condition validation errors for type=%s: %s", self.type, errors)
        return errors


# =============================================================================
# Action Types
# =============================================================================

class ActionType(str, Enum):
    """Types of actions that can be executed."""

    # Channel creation
    CREATE_CHANNEL = "create_channel"

    # Group creation
    CREATE_GROUP = "create_group"

    # Stream merging
    MERGE_STREAMS = "merge_streams"

    # Property assignment
    ASSIGN_LOGO = "assign_logo"
    ASSIGN_TVG_ID = "assign_tvg_id"
    ASSIGN_EPG = "assign_epg"
    ASSIGN_PROFILE = "assign_profile"
    ASSIGN_CHANNEL_PROFILE = "assign_channel_profile"
    SET_CHANNEL_NUMBER = "set_channel_number"

    # Variables
    SET_VARIABLE = "set_variable"

    # Stream management
    REMOVE_FROM_CHANNEL = "remove_from_channel"
    SET_STREAM_PRIORITY = "set_stream_priority"
    PROBE_STREAMS = "probe_streams"

    # Group management — post-run pass (not per-stream, see
    # channel_pipeline_engine.py Pass 3.6 / _sort_channel_groups).
    SORT_GROUP = "sort_group"

    # Control flow
    SKIP = "skip"
    STOP_PROCESSING = "stop_processing"
    LOG_MATCH = "log_match"


class IfExistsBehavior(str, Enum):
    """Behavior when target entity already exists."""
    SKIP = "skip"           # Don't create, skip this action
    MERGE = "merge"         # Add stream to existing channel (create if not found)
    MERGE_ONLY = "merge_only"  # Add stream to existing channel (skip if not found)
    UPDATE = "update"       # Update existing channel properties
    USE_EXISTING = "use_existing"  # Use existing group (for create_group)


class MergeTarget(str, Enum):
    """Target for merge_streams action."""
    NEW_CHANNEL = "new_channel"          # Create new channel with merged streams
    EXISTING_CHANNEL = "existing_channel"  # Add to existing channel
    AUTO = "auto"                         # Auto-detect based on context


class ChannelNumberStrategy(str, Enum):
    """Strategy for assigning channel numbers."""
    AUTO = "auto"           # Assign next available number
    SPECIFIC = "specific"   # Use specific number from value
    RANGE = "range"         # Use next available in range


@dataclass
class CreateChannelAction:
    """Action to create a new channel."""
    name_template: str = "{stream_name}"  # Template with variables
    channel_number: Union[str, int] = "auto"  # "auto", specific int, or "100-199" range
    group_id: Optional[int] = None  # Target group (overrides rule's target_group_id)
    if_exists: str = "skip"  # skip, merge, merge_only, update

    def to_dict(self) -> dict:
        return {
            "type": ActionType.CREATE_CHANNEL.value,
            "name_template": self.name_template,
            "channel_number": self.channel_number,
            "group_id": self.group_id,
            "if_exists": self.if_exists,
        }


@dataclass
class CreateGroupAction:
    """Action to create a new group."""
    name_template: str = "{stream_group}"  # Template with variables
    if_exists: str = "use_existing"  # skip, use_existing

    def to_dict(self) -> dict:
        return {
            "type": ActionType.CREATE_GROUP.value,
            "name_template": self.name_template,
            "if_exists": self.if_exists,
        }


@dataclass
class MergeStreamsAction:
    """Action to merge multiple streams into one channel."""
    target: str = "auto"  # new_channel, existing_channel, auto
    match_by: str = "tvg_id"  # tvg_id, normalized_name, stream_group
    find_channel_by: Optional[str] = None  # name_exact, name_regex, tvg_id (when target=existing_channel)
    find_channel_value: Optional[str] = None  # Value for find_channel_by
    quality_preference: list = field(default_factory=lambda: [1080, 720, 480])
    provider_preference: list = field(default_factory=list)  # M3U account IDs
    max_streams: int = 5

    def to_dict(self) -> dict:
        return {
            "type": ActionType.MERGE_STREAMS.value,
            "target": self.target,
            "match_by": self.match_by,
            "find_channel_by": self.find_channel_by,
            "find_channel_value": self.find_channel_value,
            "quality_preference": self.quality_preference,
            "provider_preference": self.provider_preference,
            "max_streams": self.max_streams,
        }


@dataclass
class Action:
    """
    Represents a single action to be executed.

    Actions can be simple:
        Action(type="skip")

    Or have parameters:
        Action(type="create_channel", params={
            "name_template": "{stream_name}",
            "channel_number": "auto"
        })
    """
    type: str
    params: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        result = {"type": self.type}
        result.update(self.params)
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "Action":
        """Create Action from dictionary."""
        if isinstance(data, Action):
            return data

        action_type = data.get("type", "skip")
        params = {k: v for k, v in data.items() if k != "type"}

        return cls(type=action_type, params=params)

    def validate(self) -> list[str]:
        """
        Validate the action structure.
        Returns list of error messages (empty if valid).
        """
        errors = []

        try:
            action_type = ActionType(self.type)
        except ValueError:
            logger.warning("[AUTO-CREATE-SCHEMA] Unknown action type: %s", self.type)
            errors.append(f"Unknown action type: {self.type}")
            return errors

        # Validate create_channel
        if action_type == ActionType.CREATE_CHANNEL:
            if "name_template" not in self.params:
                self.params["name_template"] = "{stream_name}"
            if_exists = self.params.get("if_exists", "skip")
            if if_exists not in ("skip", "merge", "merge_only", "update"):
                errors.append(f"create_channel.if_exists must be 'skip', 'merge', 'merge_only', or 'update'")
            # Validate optional name transform
            errors.extend(self._validate_name_transform())

        # Validate create_group
        elif action_type == ActionType.CREATE_GROUP:
            if "name_template" not in self.params:
                self.params["name_template"] = "{stream_group}"
            if_exists = self.params.get("if_exists", "use_existing")
            if if_exists not in ("skip", "use_existing"):
                errors.append(f"create_group.if_exists must be 'skip' or 'use_existing'")
            # Validate optional name transform
            errors.extend(self._validate_name_transform())

        # Validate merge_streams
        elif action_type == ActionType.MERGE_STREAMS:
            target = self.params.get("target", "auto")
            if target not in ("new_channel", "existing_channel", "auto"):
                errors.append(f"merge_streams.target must be 'new_channel', 'existing_channel', or 'auto'")

            # NOTE (bd-0emgo.1): match_by is validated here and surfaced in the
            # OpenAPI enum but is a runtime NO-OP — it is never consumed by the
            # executor. It is retained for backward compatibility of stored
            # rules. The control that actually selects fuzzy vs exact matching
            # for merge_streams target=auto is the ``loose_name_match`` boolean
            # below. Do not rely on match_by to change matching behavior.
            match_by = self.params.get("match_by", "tvg_id")
            if match_by not in ("tvg_id", "normalized_name", "stream_group"):
                errors.append(f"merge_streams.match_by must be 'tvg_id', 'normalized_name', or 'stream_group'")

            if target == "existing_channel":
                if not self.params.get("find_channel_by"):
                    errors.append("merge_streams with target='existing_channel' requires 'find_channel_by'")
                elif self.params["find_channel_by"] not in ("name_exact", "name_regex", "tvg_id"):
                    errors.append("merge_streams.find_channel_by must be 'name_exact', 'name_regex', or 'tvg_id'")

            # Optional prune toggle
            remove_non_matching = self.params.get("remove_non_matching", False)
            if remove_non_matching is not None and not isinstance(remove_non_matching, bool):
                errors.append("merge_streams.remove_non_matching must be a boolean")
            if "remove_non_matching" not in self.params:
                self.params["remove_non_matching"] = False

            # bd-0emgo.1: opt-in loose (legacy fuzzy) matching for target=auto.
            # Default False — merge into existing channel uses EXACT normalized-
            # name equality. True restores the legacy core-name / deparen /
            # word-prefix / call-sign cascade.
            loose_name_match = self.params.get("loose_name_match", False)
            if loose_name_match is not None and not isinstance(loose_name_match, bool):
                errors.append("merge_streams.loose_name_match must be a boolean")
            if "loose_name_match" not in self.params:
                self.params["loose_name_match"] = False

            # bd-0emgo.3: TARGET-CHANNEL group filter (post-resolution reject).
            # These constrain which existing channel the merge may land in, by
            # the RESOLVED channel's group — distinct from the stream-side
            # normalized_name_[not_]in_group *conditions* (which only gate
            # whether the rule FIRES). Absent = no filter (back-compat); the
            # keys are NOT auto-injected so a no-filter rule stays unchanged.
            # An empty list is valid and means "no exclusion / no restriction".
            for key in ("target_channel_not_in_group", "target_channel_in_group"):
                if key not in self.params:
                    continue
                value = self.params[key]
                if not isinstance(value, list):
                    errors.append(f"merge_streams.{key} must be a list of group IDs")
                    continue
                # bool is a subclass of int — reject it explicitly so True/False
                # cannot masquerade as a group ID.
                if any(not isinstance(item, int) or isinstance(item, bool)
                       for item in value):
                    errors.append(f"merge_streams.{key} must contain only integer group IDs")

            # enhancedchannelmanager-jnzst: SCORED-FUZZY path (Component A).
            # ``min_score`` upgrades loose_name_match from the legacy boolean
            # cascade to the unified scoring core (services.dedup_matcher).
            # Scope of the new rules is NARROW — they apply ONLY when min_score
            # is PRESENT. A legacy loose_name_match rule WITHOUT min_score keeps
            # validating and running exactly as before (the legacy cascade).
            min_score = self.params.get("min_score")
            scored_fuzzy = min_score is not None
            if scored_fuzzy:
                # min_score is a float/int in [0.0, 1.0]; bool rejected (it is
                # an int subclass and a boolean min_score is always a mistake).
                if isinstance(min_score, bool) or not isinstance(min_score, (int, float)):
                    errors.append("merge_streams.min_score must be a number in [0.0, 1.0]")
                elif not (0.0 <= float(min_score) <= 1.0):
                    errors.append("merge_streams.min_score must be between 0.0 and 1.0")
                else:
                    # Enforce the hard CONFIDENCE_FLOOR — min_score can never
                    # drop below it (ADR-008 §D2 floor; the scored path inherits
                    # the same integrity constraint as dedup). Floor is imported
                    # lazily to avoid a heavy import at module load.
                    from services.dedup_matcher import CONFIDENCE_FLOOR
                    if float(min_score) < CONFIDENCE_FLOOR:
                        errors.append(
                            f"merge_streams.min_score must be >= the confidence "
                            f"floor {CONFIDENCE_FLOOR}"
                        )

                # The scored path REQUIRES loose_name_match=True — min_score has
                # no meaning on the exact path.
                if not loose_name_match:
                    errors.append(
                        "merge_streams.min_score requires loose_name_match=true "
                        "(the scored fuzzy path)"
                    )

                # Q2 SCOPING: a scored-fuzzy rule MUST be scoped to an allowlist
                # of target groups. An unscoped fuzzy rule is the over-matching
                # foot-gun the incident was about — refuse it.
                allowlist = self.params.get("target_channel_in_group") or []
                if not (isinstance(allowlist, list) and len(allowlist) > 0):
                    errors.append(
                        "merge_streams.min_score (scored fuzzy) requires a "
                        "non-empty target_channel_in_group allowlist"
                    )

                # Q1 NO-CALLSIGN POLICY: by default a scored-fuzzy rule REQUIRES
                # a parseable callsign on both sides. ``allow_no_callsign`` is an
                # explicit opt-in; when set, no-callsign pairs are admitted only
                # at score >= NO_CALLSIGN_FLOOR (enforced in the executor).
                allow_no_callsign = self.params.get("allow_no_callsign", False)
                if not isinstance(allow_no_callsign, bool):
                    errors.append("merge_streams.allow_no_callsign must be a boolean")

                # Optional tie_break / max_candidates.
                tie_break = self.params.get("tie_break")
                if tie_break is not None and tie_break not in ("lowest_id", "highest_score"):
                    errors.append(
                        "merge_streams.tie_break must be 'lowest_id' or 'highest_score'"
                    )
                max_candidates = self.params.get("max_candidates")
                if max_candidates is not None:
                    if isinstance(max_candidates, bool) or not isinstance(max_candidates, int):
                        errors.append("merge_streams.max_candidates must be a positive integer")
                    elif max_candidates < 1:
                        errors.append("merge_streams.max_candidates must be >= 1")

        # Validate assign_logo
        elif action_type == ActionType.ASSIGN_LOGO:
            value = self.params.get("value")
            if value is None:
                errors.append("assign_logo requires a 'value' (URL, 'from_stream', or 'from_epg')")
            elif value == "from_epg" and not self.params.get("epg_id"):
                errors.append("assign_logo with 'from_epg' requires an 'epg_id'")

        # Validate assign_tvg_id
        elif action_type == ActionType.ASSIGN_TVG_ID:
            value = self.params.get("value")
            if value is None:
                errors.append("assign_tvg_id requires a 'value' (string or 'from_stream')")

        # Validate assign_epg
        elif action_type == ActionType.ASSIGN_EPG:
            epg_id = self.params.get("epg_id")
            if epg_id is None or not isinstance(epg_id, int):
                errors.append("assign_epg requires an 'epg_id' (integer)")
            set_tvg_id = self.params.get("set_tvg_id")
            if set_tvg_id is not None and not isinstance(set_tvg_id, bool):
                errors.append("assign_epg.set_tvg_id must be a boolean")

        # Validate assign_profile
        elif action_type == ActionType.ASSIGN_PROFILE:
            profile_id = self.params.get("profile_id")
            if profile_id is None or not isinstance(profile_id, int):
                errors.append("assign_profile requires a 'profile_id' (integer)")

        # Validate assign_channel_profile
        elif action_type == ActionType.ASSIGN_CHANNEL_PROFILE:
            channel_profile_ids = self.params.get("channel_profile_ids")
            if not channel_profile_ids or not isinstance(channel_profile_ids, list):
                errors.append("assign_channel_profile requires 'channel_profile_ids' (list of integers)")
            elif not all(isinstance(pid, int) for pid in channel_profile_ids):
                errors.append("assign_channel_profile 'channel_profile_ids' must all be integers")

        # Validate set_channel_number
        elif action_type == ActionType.SET_CHANNEL_NUMBER:
            value = self.params.get("value")
            if value is None:
                errors.append("set_channel_number requires a 'value' ('auto', integer, or 'min-max' range)")

        # Validate set_stream_priority
        elif action_type == ActionType.SET_STREAM_PRIORITY:
            priority = self.params.get("priority", "lowest")
            if priority not in ("lowest", "highest"):
                errors.append("set_stream_priority.priority must be 'lowest' or 'highest'")

        # Validate log_match
        elif action_type == ActionType.LOG_MATCH:
            message = self.params.get("message")
            if not message:
                errors.append("log_match requires a 'message'")

        # Validate set_variable
        elif action_type == ActionType.SET_VARIABLE:
            var_name = self.params.get("variable_name")
            if not var_name or not isinstance(var_name, str):
                errors.append("set_variable requires a 'variable_name' (string)")
            elif not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', var_name):
                errors.append("set_variable.variable_name must be alphanumeric with underscores")

            mode = self.params.get("variable_mode")
            if mode not in ("regex_extract", "regex_replace", "literal"):
                errors.append("set_variable.variable_mode must be 'regex_extract', 'regex_replace', or 'literal'")
            else:
                if mode in ("regex_extract", "regex_replace"):
                    source = self.params.get("source_field")
                    if not source or not isinstance(source, str):
                        errors.append(f"set_variable with mode '{mode}' requires a 'source_field'")
                    pattern = self.params.get("pattern")
                    if not pattern or not isinstance(pattern, str):
                        errors.append(f"set_variable with mode '{mode}' requires a 'pattern'")
                    else:
                        # bd-ltjyx: route through safe_regex.compile so the
                        # length cap and compile-error surface match the
                        # runtime path that actually executes this pattern
                        # (channel_pipeline_executor._execute_set_variable).
                        try:
                            safe_regex.compile(pattern)
                        except safe_regex.SafeRegexError as e:
                            errors.append(f"Invalid regex pattern for set_variable: {e}")
                    if mode == "regex_replace":
                        replacement = self.params.get("replacement")
                        if replacement is None or not isinstance(replacement, str):
                            errors.append("set_variable with mode 'regex_replace' requires a 'replacement'")
                elif mode == "literal":
                    template = self.params.get("template")
                    if not template or not isinstance(template, str):
                        errors.append("set_variable with mode 'literal' requires a 'template'")

        # Validate sort_group (enhancedchannelmanager-vy4fl). This is an
        # ACTION, not a condition — it never touches
        # channel_pipeline_rule_analyzer._GUARD_TYPES (which classifies
        # CONDITION types only). Fires once per group per run (post-run
        # pass — channel_pipeline_engine.py Pass 3.6), deduplicated by
        # group_id regardless of how many matched streams land in that
        # group. Sort semantics are the backend port of the manual
        # Sort & Renumber modal — see channel_pipeline_sort.py.
        elif action_type == ActionType.SORT_GROUP:
            order = self.params.get("order", "asc")
            if order not in ("asc", "desc"):
                errors.append("sort_group.order must be 'asc' or 'desc'")
            if "order" not in self.params:
                self.params["order"] = "asc"

            starting_number = self.params.get("starting_number")
            if starting_number is not None:
                # bool is an int subclass — reject it explicitly, same
                # convention as target_channel_in_group elsewhere in this
                # module.
                if isinstance(starting_number, bool) or not isinstance(starting_number, int):
                    errors.append("sort_group.starting_number must be an integer")
                elif starting_number < 1:
                    errors.append("sort_group.starting_number must be >= 1")

            group_id = self.params.get("group_id")
            if group_id is not None and (isinstance(group_id, bool) or not isinstance(group_id, int)):
                errors.append("sort_group.group_id must be an integer")

            strip_numbers = self.params.get("strip_numbers", True)
            if not isinstance(strip_numbers, bool):
                errors.append("sort_group.strip_numbers must be a boolean")
            if "strip_numbers" not in self.params:
                self.params["strip_numbers"] = True

            ignore_country = self.params.get("ignore_country", False)
            if not isinstance(ignore_country, bool):
                errors.append("sort_group.ignore_country must be a boolean")
            if "ignore_country" not in self.params:
                self.params["ignore_country"] = False

        if errors:
            logger.warning("[AUTO-CREATE-SCHEMA] Action validation errors for type=%s: %s", self.type, errors)
        return errors

    def _validate_name_transform(self) -> list[str]:
        """Validate optional name_transform_pattern/name_transform_replacement fields."""
        errors = []
        pattern = self.params.get("name_transform_pattern")
        replacement = self.params.get("name_transform_replacement")

        if pattern is not None:
            if not isinstance(pattern, str):
                errors.append("name_transform_pattern must be a string")
            else:
                # bd-ltjyx: route through safe_regex.compile so the length
                # cap and compile-error surface match the runtime path
                # (channel_pipeline_executor._apply_name_transform).
                try:
                    safe_regex.compile(pattern)
                except safe_regex.SafeRegexError as e:
                    errors.append(f"Invalid name_transform_pattern: {e}")
                else:
                    # enhancedchannelmanager-2uwi3: cross-check $N group
                    # references in the replacement against the pattern's
                    # capture-group count. At execution time an out-of-range
                    # reference raises lazily inside regex.sub (only on
                    # matching inputs) and is swallowed into a silent no-op
                    # — reject it at save time with an actionable message.
                    if isinstance(replacement, str):
                        for violation in lint_replacement_group_refs(
                            pattern, replacement,
                            field="name_transform_replacement",
                        ):
                            errors.append(
                                f"Invalid name_transform_replacement: "
                                f"{violation.message}"
                            )
            # replacement is optional (defaults to empty string), but must be string if present
            if replacement is not None and not isinstance(replacement, str):
                errors.append("name_transform_replacement must be a string")

        return errors


# =============================================================================
# Template Variables
# =============================================================================

class TemplateVariables:
    """
    Available template variables for name templates.

    Variables can be used in name_template fields like:
        "{stream_name} - {quality}"
    """

    STREAM_NAME = "stream_name"          # Original stream name
    STREAM_GROUP = "stream_group"        # Stream's group name
    TVG_ID = "tvg_id"                    # Stream's EPG ID
    TVG_NAME = "tvg_name"                # Stream's EPG name
    QUALITY = "quality"                  # Resolution as string (e.g., "1080p", "720p")
    QUALITY_RAW = "quality_raw"          # Resolution as number (e.g., 1080, 720)
    PROVIDER = "provider"                # M3U account name
    PROVIDER_ID = "provider_id"          # M3U account ID
    NORMALIZED_NAME = "normalized_name"  # Name after normalization rules

    @classmethod
    def all_variables(cls) -> list[str]:
        """Return all available variable names."""
        return [
            cls.STREAM_NAME,
            cls.STREAM_GROUP,
            cls.TVG_ID,
            cls.TVG_NAME,
            cls.QUALITY,
            cls.QUALITY_RAW,
            cls.PROVIDER,
            cls.PROVIDER_ID,
            cls.NORMALIZED_NAME,
        ]

    @staticmethod
    def expand_template(template: str, context: dict, custom_variables: dict = None) -> str:
        """
        Expand a template string with context variables.

        Args:
            template: String with {variable} placeholders
            context: Dict mapping variable names to values
            custom_variables: Optional dict of custom variables (accessible as {var:name})

        Returns:
            Expanded string with variables replaced
        """
        result = template
        for var_name, value in context.items():
            placeholder = "{" + var_name + "}"
            if placeholder in result:
                result = result.replace(placeholder, str(value) if value else "")
        # Expand custom variables ({var:name})
        if custom_variables:
            for var_name, value in custom_variables.items():
                placeholder = "{var:" + var_name + "}"
                if placeholder in result:
                    result = result.replace(placeholder, str(value) if value else "")
        expanded = result.strip()
        if expanded != template:
            logger.debug("[AUTO-CREATE-SCHEMA] Template '%s' -> '%s'", template, expanded)
        return expanded


# =============================================================================
# Event Sync config validation (enhancedchannelmanager-ti939.1.3)
# =============================================================================

# Keys accepted at the top level of event_sync_config. Anything else is
# rejected — a typo'd optional key ("attach_treshold") would otherwise
# silently fall back to its default, which for a threshold is a safety knob.
_EVENT_SYNC_ALLOWED_KEYS = frozenset({
    # Provider-scoped canonical shape (bead 3p2af).
    "master",
    "secondary",
    # Legacy flat shape — still accepted as input and kept in sync as derived
    # keys so existing readers work (validate upgrades legacy -> nested).
    "master_group_id",
    "secondary_group_ids",
    "patterns",
    "group_patterns",
    "time_window_minutes",
    "enforce_time_window",
    "attach_threshold",
    "max_attach_per_run",
    "enabled",
    "auto_run",
    "refresh_providers_before_run",
    "dummy_epg_profile_id",
    "include_master_group_streams",
    "assume_current_date",
    "parse_master_from_stream",
})

# Ceiling for event_sync_config.max_attach_per_run. The cap is a blast-radius
# control (bead ti939.2.1) — an effectively-unbounded value would defeat it,
# so the config cannot raise it past this.
_EVENT_SYNC_MAX_ATTACH_CEILING = 1000

# Keys accepted inside one parse-pattern variant (mirrors the shape of
# services.event_sync_matcher.DEFAULT_EVENT_PATTERNS entries, which is what
# parse_event_name() consumes).
_EVENT_SYNC_PATTERN_KEYS = frozenset({
    "name",
    "title_pattern",
    "time_pattern",
    "date_pattern",
})

# Pattern fields that must compile via safe_regex when present. Operator
# regex is the ReDoS surface (bd-ltjyx precedent) — these execute against
# untrusted provider strings at preview/run time via
# dummy_epg_engine.extract_groups → safe_regex, so save-time validation
# must route through the exact same compiler.
_EVENT_SYNC_PATTERN_REGEX_FIELDS = ("title_pattern", "time_pattern", "date_pattern")


def _event_sync_error(field: str, got: Any, expected: str) -> str:
    """Teaching validation error: field, got, expected, doc link."""
    return (
        f"event_sync_config.{field}: got {got!r}, expected {expected} "
        f"(see docs/event_sync.md)"
    )


def _validate_event_sync_patterns(patterns: Any, field: str) -> list[str]:
    """Validate one list of parse-pattern variants (shared or per-group)."""
    errors: list[str] = []
    if not isinstance(patterns, list) or not patterns:
        errors.append(_event_sync_error(
            field, patterns,
            "a non-empty list of pattern objects "
            "(omit the key entirely to use the built-in default patterns)",
        ))
        return errors

    for i, variant in enumerate(patterns):
        vfield = f"{field}[{i}]"
        if not isinstance(variant, dict):
            errors.append(_event_sync_error(
                vfield, variant,
                "an object with title_pattern (required) and optional "
                "name/time_pattern/date_pattern",
            ))
            continue

        unknown = sorted(set(variant) - _EVENT_SYNC_PATTERN_KEYS)
        if unknown:
            errors.append(_event_sync_error(
                vfield, unknown,
                f"only the keys {sorted(_EVENT_SYNC_PATTERN_KEYS)}",
            ))

        name = variant.get("name")
        if name is not None and not isinstance(name, str):
            errors.append(_event_sync_error(
                f"{vfield}.name", name, "a string label",
            ))

        title_pattern = variant.get("title_pattern")
        if not title_pattern or not isinstance(title_pattern, str):
            errors.append(_event_sync_error(
                f"{vfield}.title_pattern", title_pattern,
                "a non-empty regex string with a (?P<title>...) capture group",
            ))

        for key in _EVENT_SYNC_PATTERN_REGEX_FIELDS:
            pattern = variant.get(key)
            if pattern is None:
                continue
            if not isinstance(pattern, str):
                errors.append(_event_sync_error(
                    f"{vfield}.{key}", pattern, "a regex string",
                ))
                continue
            # bd-ltjyx: route through safe_regex.compile so the length cap
            # and compile-error surface match the runtime path
            # (dummy_epg_engine.extract_groups → safe_regex).
            try:
                safe_regex.compile(pattern)
            except safe_regex.SafeRegexError as e:
                errors.append(_event_sync_error(
                    f"{vfield}.{key}", pattern,
                    f"a regex that compiles under safe_regex ({e})",
                ))

    return errors


def _is_group_id(value: Any) -> bool:
    """Positive integer group ID; bool is an int subclass — reject it."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _is_provider_id(value: Any) -> bool:
    """m3u_account_id: a positive int, or None (= whole group / any provider)."""
    return value is None or _is_group_id(value)


def _normalize_scope(raw: Any) -> dict | None:
    """Normalize one provider-scoped group to ``{group_id, m3u_account_id}``.

    Accepts the nested form (``{"group_id": int, "m3u_account_id": int|null}``)
    or a bare integer group id (legacy shape — upgraded here on read, so no
    data migration is needed). Returns ``None`` when the shape is invalid.
    ``m3u_account_id`` of ``None`` means "the whole group / any provider" —
    the pre-provider-scope behavior.
    """
    if _is_group_id(raw):
        return {"group_id": raw, "m3u_account_id": None}
    if isinstance(raw, dict):
        gid = raw.get("group_id")
        pid = raw.get("m3u_account_id")
        if _is_group_id(gid) and _is_provider_id(pid):
            return {"group_id": gid, "m3u_account_id": pid}
    return None


def validate_event_sync_config(config: Any) -> list[str]:
    """Validate (and default-fill) an event_sync rule config at save time.

    Event Sync (epic ti939): one channel per live event — Dispatcharr owns
    the MASTER group's channel lifecycle (auto_channel_sync ON); ECM matches
    SECONDARY groups' streams onto those channels. This validator is the
    schema-enforced rail set:

    * **Mandatory scoping** — master_group_id present, secondary_group_ids
      non-empty, master not in secondaries. Scoping is what prevented
      recurrence of the 1,341 false-positive merge incident: an unscoped
      event rule is refused at save time, not by convention.
    * **safe_regex at save time** — operator parse regexes are the ReDoS
      surface (bd-ltjyx); they compile through the exact compiler the
      runtime uses.
    * **attach_threshold** — any value in [0.0, 1.0].
      services.event_sync_matcher.EVENT_ATTACH_FLOOR (0.80) is the DEFAULT,
      not a hard floor (bead krkm4-sibling): the value is
      operator-authoritative and may be lowered per rule. The default
      constant is IMPORTED from the matcher — the single source of truth —
      and is_event_attachable() honors the stored value directly at runtime,
      so the schema and execution layers cannot drift apart.

    Mirrors Action.validate()'s convention of filling defaults in place
    (time_window_minutes, attach_threshold, enabled) so stored configs are
    explicit. Returns a list of teaching error strings (field, got,
    expected, doc link) — empty when valid.
    """
    # Floor/defaults live in the matcher service (single source of truth —
    # do NOT redeclare them here). Imported lazily to keep the heavy matcher
    # module (rapidfuzz, pytz, dummy-EPG engine) off this module's load path,
    # same pattern as dedup_matcher.CONFIDENCE_FLOOR above.
    from services.event_sync_matcher import (
        DEFAULT_TIME_WINDOW_MINUTES,
        EVENT_ATTACH_FLOOR,
    )
    from services.event_sync_resolver import DEFAULT_MAX_ATTACH_PER_RUN

    errors: list[str] = []

    if not isinstance(config, dict):
        return [_event_sync_error(
            "", config,
            "a JSON object with master_group_id, secondary_group_ids and "
            "optional patterns/group_patterns/time_window_minutes/"
            "attach_threshold/max_attach_per_run/enabled",
        )]

    unknown = sorted(set(config) - _EVENT_SYNC_ALLOWED_KEYS)
    if unknown:
        errors.append(_event_sync_error(
            "", unknown,
            f"only the keys {sorted(_EVENT_SYNC_ALLOWED_KEYS)}",
        ))

    # --- Mandatory scoping (provider-scoped, beads 3p2af/2c2vz) ----------
    # Canonical shape is nested and provider-scoped:
    #   master:    {group_id, m3u_account_id}
    #   secondary: [{group_id, m3u_account_id}, ...]
    # ``m3u_account_id`` is None for "the whole group / any provider" (the
    # pre-provider-scope behavior). A legacy flat config
    # ({master_group_id, secondary_group_ids}) is UPGRADED here on read (no
    # data migration). The derived flat keys are kept in sync below so every
    # existing reader of master_group_id / secondary_group_ids is untouched;
    # only the provider-aware fetch/preflight read the nested provider ids.
    include_master_streams = config.get("include_master_group_streams") is True

    raw_master = config.get("master")
    if raw_master is None and "master_group_id" in config:
        raw_master = config.get("master_group_id")
    master_scope = _normalize_scope(raw_master)
    if master_scope is None:
        errors.append(_event_sync_error(
            "master_group_id", raw_master,
            'the master event group as a positive integer group id, or '
            '{"group_id": <positive int>, "m3u_account_id": <positive int or '
            "null>} (null = whole group / any provider) — the ONE group whose "
            "channels Dispatcharr owns (auto_channel_sync ON)",
        ))

    raw_secondary = config.get("secondary")
    if raw_secondary is None and "secondary_group_ids" in config:
        raw_secondary = config.get("secondary_group_ids")
    # bead 3ux85: an empty secondary list is allowed ONLY when the master
    # group is itself the stream source (include_master_group_streams).
    if raw_secondary is None and include_master_streams:
        raw_secondary = []
    secondary_scopes: list[dict] = []
    secondary_ok = True
    if not isinstance(raw_secondary, list):
        secondary_ok = False
        errors.append(_event_sync_error(
            "secondary_group_ids", raw_secondary,
            'a list of secondary event groups — each a positive integer group '
            'id or {"group_id": int, "m3u_account_id": int|null}',
        ))
    else:
        for item in raw_secondary:
            sc = _normalize_scope(item)
            if sc is None:
                secondary_ok = False
                errors.append(_event_sync_error(
                    "secondary_group_ids", item,
                    'each secondary as a positive integer group id or '
                    '{"group_id": int, "m3u_account_id": int|null}',
                ))
            else:
                secondary_scopes.append(sc)
        if secondary_ok and not secondary_scopes and not include_master_streams:
            errors.append(_event_sync_error(
                "secondary_group_ids", raw_secondary,
                "a non-empty list — an unscoped event rule is refused. (An "
                "empty list is allowed only when include_master_group_streams "
                "is true, so the master group is itself the stream source.)",
            ))

    # The master-not-in-secondary rail is now the (group, provider) PAIR: the
    # SAME group with a DIFFERENT provider IS a valid secondary (that is the
    # whole point of provider scoping), but an identical group+provider cannot
    # be both master and secondary.
    if master_scope is not None and secondary_ok:
        m_pair = (master_scope["group_id"], master_scope["m3u_account_id"])
        if any((sc["group_id"], sc["m3u_account_id"]) == m_pair
               for sc in secondary_scopes):
            errors.append(_event_sync_error(
                "secondary_group_ids", secondary_scopes,
                "a list whose (group, provider) does not duplicate "
                "master_group_id's — an identical group+provider cannot be "
                "both master and secondary. (The same group with a DIFFERENT "
                "provider IS allowed.)",
            ))

    # Write canonical nested + derived flat. Downstream readers use the flat
    # keys (unchanged); provider-aware code reads master/secondary.
    if master_scope is not None:
        config["master"] = master_scope
        config["master_group_id"] = master_group_id = master_scope["group_id"]
    else:
        master_group_id = None
    if secondary_ok:
        config["secondary"] = secondary_scopes
        config["secondary_group_ids"] = secondary_group_ids = [
            sc["group_id"] for sc in secondary_scopes
        ]
    else:
        secondary_group_ids = []

    # --- Parse patterns (shared and per-group) ---------------------------
    if "patterns" in config:
        errors.extend(_validate_event_sync_patterns(
            config["patterns"], "patterns"
        ))

    if "group_patterns" in config:
        group_patterns = config["group_patterns"]
        if not isinstance(group_patterns, dict):
            errors.append(_event_sync_error(
                "group_patterns", group_patterns,
                "an object mapping group ID -> list of pattern objects",
            ))
        else:
            in_scope = set(secondary_group_ids)
            if _is_group_id(master_group_id):
                in_scope.add(master_group_id)
            for raw_key, patterns in group_patterns.items():
                # JSON object keys are strings; accept int for dict callers.
                try:
                    group_id = int(raw_key)
                except (TypeError, ValueError):
                    errors.append(_event_sync_error(
                        f"group_patterns[{raw_key!r}]", raw_key,
                        "an integer group ID key",
                    ))
                    continue
                if group_id not in in_scope:
                    errors.append(_event_sync_error(
                        f"group_patterns[{raw_key!r}]", group_id,
                        "a group ID that is the master_group_id or one of "
                        "secondary_group_ids — per-group patterns for a "
                        "group outside the rule's scope would never run",
                    ))
                errors.extend(_validate_event_sync_patterns(
                    patterns, f"group_patterns[{raw_key!r}]"
                ))

    # --- Matching knobs ---------------------------------------------------
    time_window_minutes = config.get("time_window_minutes")
    if time_window_minutes is None:
        config["time_window_minutes"] = DEFAULT_TIME_WINDOW_MINUTES
    elif (isinstance(time_window_minutes, bool)
            or not isinstance(time_window_minutes, int)
            or not (1 <= time_window_minutes <= 1440)):
        # Ceiling 1440 = 24h (PR #612 review): the time window is the rail
        # that keeps same-teams-different-day fixtures apart — an oversized
        # window re-opens that false-positive class, and the frozen corpus
        # only proves the trap at sane windows.
        errors.append(_event_sync_error(
            "time_window_minutes", time_window_minutes,
            f"an integer number of minutes between 1 and 1440 (default "
            f"{DEFAULT_TIME_WINDOW_MINUTES}) — parsed start times must be "
            f"within ± this window to become candidate pairs; a window "
            f"over 24h would re-admit same-teams-different-day pairs the "
            f"matcher's precision guarantees do not cover",
        ))

    # enforce_time_window (bead krkm4): opt-out of the time-window gate.
    # Defaults True (gate enforced at time_window_minutes). When False the
    # resolver passes window=None to the matcher — ranking by title/team
    # alone. time_window_minutes stays validated above regardless, so it
    # keeps its meaning if the operator re-enables the gate later.
    enforce_time_window = config.get("enforce_time_window")
    if enforce_time_window is None:
        config["enforce_time_window"] = True
    elif not isinstance(enforce_time_window, bool):
        errors.append(_event_sync_error(
            "enforce_time_window", enforce_time_window,
            "a boolean — True (default) enforces the time-window candidacy "
            "gate at time_window_minutes; False disables it so streams match "
            "on title/team alone (safe only for a single-provider, same-day "
            "master group — recurring/serial titles risk cross-day matches)",
        ))

    attach_threshold = config.get("attach_threshold")
    if attach_threshold is None:
        config["attach_threshold"] = EVENT_ATTACH_FLOOR
    elif (isinstance(attach_threshold, bool)
            or not isinstance(attach_threshold, (int, float))
            or not (0.0 <= float(attach_threshold) <= 1.0)):
        # bead krkm4-sibling (PO decision): EVENT_ATTACH_FLOOR (0.80) is the
        # DEFAULT, not a hard clamp. attach_threshold is operator-authoritative
        # and may be any value in [0.0, 1.0] — a rule whose provider data needs
        # a lower auto-attach bar (e.g. teamless events whose master titles
        # carry slot/venue noise) can set one. is_event_attachable() honors the
        # value directly; the team-conflict and numeric-identity hard-reject
        # rails still fire at any threshold, so lowering the floor trades
        # precision for recall without reopening the contradiction classes.
        errors.append(_event_sync_error(
            "attach_threshold", attach_threshold,
            "a number in [0.0, 1.0]",
        ))

    # Per-run attach cap (bead ti939.2.1 — Phase 1B blast-radius control).
    # The existing created-channel cap does not cover merge/attach
    # operations, so the attach path carries its own. Default lives in
    # services.event_sync_resolver (single source of truth, lazily imported
    # above alongside the matcher constants).
    max_attach_per_run = config.get("max_attach_per_run")
    if max_attach_per_run is None:
        config["max_attach_per_run"] = DEFAULT_MAX_ATTACH_PER_RUN
    elif (isinstance(max_attach_per_run, bool)
            or not isinstance(max_attach_per_run, int)
            or not (1 <= max_attach_per_run <= _EVENT_SYNC_MAX_ATTACH_CEILING)):
        errors.append(_event_sync_error(
            "max_attach_per_run", max_attach_per_run,
            f"an integer between 1 and {_EVENT_SYNC_MAX_ATTACH_CEILING} "
            f"(default {DEFAULT_MAX_ATTACH_PER_RUN}) — the per-run attach "
            f"cap; on overage the run stops attaching, warns in the "
            f"execution log and records the overage count",
        ))

    enabled = config.get("enabled")
    if enabled is None:
        config["enabled"] = True
    elif not isinstance(enabled, bool):
        errors.append(_event_sync_error(
            "enabled", enabled, "a boolean",
        ))

    # Phase 2 opt-in auto-run flag (bead ti939.3.1). DEFAULT OFF is a safety
    # property, not a convenience default: an event_sync rule only ever runs
    # from the unattended refresh-watermark task when the operator explicitly
    # set this to true (after trusting manual runs). Absent == false, so
    # every stored config that predates the key keeps manual-run-only
    # behavior unchanged. Filled in place like the other defaults so stored
    # configs are explicit.
    auto_run = config.get("auto_run")
    if auto_run is None:
        config["auto_run"] = False
    elif not isinstance(auto_run, bool):
        errors.append(_event_sync_error(
            "auto_run", auto_run,
            "a boolean (default false) — true opts this rule into "
            "unattended runs on the M3U refresh watermark task",
        ))

    # Pre-refresh-on-run flag (bead y8yby). DEFAULT OFF. When true, a MANUAL
    # run of this rule (live Run AND dry-run Test) first refreshes the M3U
    # provider accounts backing the rule's master + secondary groups, then
    # runs the match/attach — so the run works against fresh provider data,
    # closing the refresh-ordering staleness window. It deliberately does NOT
    # apply to the unattended auto-run path (that already follows a refresh —
    # pre-refreshing there would be circular). Absent == false, so stored
    # configs that predate the key keep exactly their prior behavior. NOTE
    # (surfaced in the UI): with this ON the dry-run Test triggers a real
    # Dispatcharr provider refresh, so Test is no longer zero-write.
    refresh_before_run = config.get("refresh_providers_before_run")
    if refresh_before_run is None:
        config["refresh_providers_before_run"] = False
    elif not isinstance(refresh_before_run, bool):
        errors.append(_event_sync_error(
            "refresh_providers_before_run", refresh_before_run,
            "a boolean (default false) — true refreshes the rule's M3U "
            "providers before a manual Run or Test (making Test no longer "
            "zero-write); it never applies to unattended auto-runs",
        ))

    # Master-group self-attach flag (bead 6xxmp). DEFAULT OFF. When true the
    # MASTER group's own streams are ALSO fetched as a secondary source and
    # matched to the master channels — the sanctioned path for the case a
    # single Dispatcharr channel group (global, unique-by-name) carries
    # streams from TWO providers, one auto-synced (its streams already on the
    # master channels) and one not: the unsynced provider's streams attach to
    # the synced provider's event channels. The master group is NEVER added
    # to secondary_group_ids (that rail stays); the resolver filters the
    # master group's already-attached streams so preview/run parity holds and
    # the auto-synced provider's own streams are not re-offered.
    include_master = config.get("include_master_group_streams")
    if include_master is None:
        config["include_master_group_streams"] = False
    elif not isinstance(include_master, bool):
        errors.append(_event_sync_error(
            "include_master_group_streams", include_master,
            "a boolean (default false) — true also matches the master "
            "group's own unattached streams (a second provider sharing the "
            "master group's name) to the master channels",
        ))

    # Dateless "today's schedule" opt-in (bead assume-current-date). DEFAULT
    # OFF — it deliberately relaxes the never-guess-the-date rail (a listing
    # with a time but no date is placed on the current date), so it must be
    # an explicit per-rule choice with the cross-day risk understood.
    assume_current_date = config.get("assume_current_date")
    if assume_current_date is None:
        config["assume_current_date"] = False
    elif not isinstance(assume_current_date, bool):
        errors.append(_event_sync_error(
            "assume_current_date", assume_current_date,
            "a boolean (default false) — true fills the CURRENT date for "
            "listings that carry a time but no date (dateless live "
            "schedules), accepting the cross-day match risk",
        ))

    # Parse master identity from the attached stream (bead parse-from-stream).
    # DEFAULT OFF. When true the master event's title+time are read from each
    # master channel's FIRST attached stream name instead of the channel
    # name, so operators can name master channels freely.
    parse_master_from_stream = config.get("parse_master_from_stream")
    if parse_master_from_stream is None:
        config["parse_master_from_stream"] = False
    elif not isinstance(parse_master_from_stream, bool):
        errors.append(_event_sync_error(
            "parse_master_from_stream", parse_master_from_stream,
            "a boolean (default false) — true reads each master channel's "
            "event identity from its attached stream's name instead of the "
            "channel name, so master channels can be named freely",
        ))

    # Dummy EPG auto-assignment (bead ti939.3.3). OPTIONAL — an absent key
    # means the feature is OFF and is NOT default-filled (unlike the knobs
    # above): filling e.g. null would turn a feature toggle into stored
    # noise. When present, the reference must RESOLVE — a dangling profile
    # id is config corruption, caught here (save time AND the engine's
    # per-run re-validation) rather than as a silent no-op at run time.
    # Enabled-ness is deliberately NOT validated here: a temporarily
    # disabled profile must not invalidate the rule and kill its attach
    # path — the engine skips just the EPG-assignment step with a warning.
    dummy_epg_profile_id = config.get("dummy_epg_profile_id")
    if dummy_epg_profile_id is not None:
        if (isinstance(dummy_epg_profile_id, bool)
                or not isinstance(dummy_epg_profile_id, int)
                or dummy_epg_profile_id < 1):
            errors.append(_event_sync_error(
                "dummy_epg_profile_id", dummy_epg_profile_id,
                "a positive integer id of an existing dummy EPG profile "
                "(omit the key entirely to disable dummy EPG "
                "auto-assignment for master channels)",
            ))
        else:
            # Lazy imports: keep the DB stack off this module's load path
            # (same pattern as the matcher constants above). One quick
            # primary-key lookup, only when the key is present.
            from database import get_session
            from models import DummyEPGProfile
            db = get_session()
            try:
                profile = db.get(DummyEPGProfile, dummy_epg_profile_id)
            finally:
                db.close()
            if profile is None:
                errors.append(_event_sync_error(
                    "dummy_epg_profile_id", dummy_epg_profile_id,
                    "the id of an EXISTING dummy EPG profile — no profile "
                    "with this id was found (create one under EPG Manager "
                    "→ Dummy EPG, or omit the key to disable dummy EPG "
                    "auto-assignment)",
                ))

    if errors:
        logger.warning(
            "[AUTO-CREATE-SCHEMA] event_sync_config validation failed with "
            "%s error(s): %s", len(errors), errors,
        )
    return errors


# =============================================================================
# Validation Utilities
# =============================================================================

def validate_rule(conditions: list, actions: list) -> dict:
    """
    Validate a complete rule's conditions and actions.

    Args:
        conditions: List of condition dicts
        actions: List of action dicts

    Returns:
        Dict with 'valid' bool and 'errors' list
    """
    errors = []

    # Validate conditions
    if not conditions:
        errors.append("Rule must have at least one condition")
    else:
        for i, cond_data in enumerate(conditions):
            cond = Condition.from_dict(cond_data)
            cond_errors = cond.validate()
            errors.extend([f"conditions[{i}]: {e}" for e in cond_errors])

    # Validate actions
    if not actions:
        errors.append("Rule must have at least one action")
    else:
        for i, action_data in enumerate(actions):
            action = Action.from_dict(action_data)
            action_errors = action.validate()
            errors.extend([f"actions[{i}]: {e}" for e in action_errors])

    valid = len(errors) == 0
    if not valid:
        logger.warning("[AUTO-CREATE-SCHEMA] Rule validation failed with %s error(s): %s", len(errors), errors)
    else:
        logger.debug("[AUTO-CREATE-SCHEMA] Rule validation passed (%s conditions, %s actions)", len(conditions), len(actions))
    return {
        "valid": valid,
        "errors": errors
    }


def parse_conditions(conditions_json: str) -> list[Condition]:
    """Parse JSON string into list of Condition objects."""
    try:
        data = json.loads(conditions_json) if isinstance(conditions_json, str) else conditions_json
        conditions = [Condition.from_dict(c) for c in data]
        logger.debug("[AUTO-CREATE-SCHEMA] Parsed %s conditions", len(conditions))
        return conditions
    except (json.JSONDecodeError, TypeError, KeyError) as e:
        logger.error("[AUTO-CREATE-SCHEMA] Failed to parse conditions JSON: %s", e)
        raise ValueError(f"Invalid conditions JSON: {e}")


def parse_actions(actions_json: str) -> list[Action]:
    """Parse JSON string into list of Action objects."""
    try:
        data = json.loads(actions_json) if isinstance(actions_json, str) else actions_json
        actions = [Action.from_dict(a) for a in data]
        logger.debug("[AUTO-CREATE-SCHEMA] Parsed %s actions", len(actions))
        return actions
    except (json.JSONDecodeError, TypeError, KeyError) as e:
        logger.error("[AUTO-CREATE-SCHEMA] Failed to parse actions JSON: %s", e)
        raise ValueError(f"Invalid actions JSON: {e}")
