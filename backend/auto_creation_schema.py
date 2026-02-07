"""
Auto-Creation Pipeline Schema Definitions

Defines the structure of conditions and actions used in auto-creation rules.
Includes validation, parsing, and serialization utilities.
"""
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional, Union
import re
import json


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
                try:
                    re.compile(self.value)
                except re.error as e:
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

        elif cond_type in (ConditionType.TVG_ID_EXISTS, ConditionType.LOGO_EXISTS, ConditionType.HAS_CHANNEL):
            if self.value is not None and not isinstance(self.value, bool):
                errors.append(f"{self.type} value should be boolean or omitted")

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
    SET_CHANNEL_NUMBER = "set_channel_number"

    # Variables
    SET_VARIABLE = "set_variable"

    # Control flow
    SKIP = "skip"
    STOP_PROCESSING = "stop_processing"
    LOG_MATCH = "log_match"


class IfExistsBehavior(str, Enum):
    """Behavior when target entity already exists."""
    SKIP = "skip"           # Don't create, skip this action
    MERGE = "merge"         # Add stream to existing channel
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
    if_exists: str = "skip"  # skip, merge, update

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
            errors.append(f"Unknown action type: {self.type}")
            return errors

        # Validate create_channel
        if action_type == ActionType.CREATE_CHANNEL:
            if "name_template" not in self.params:
                self.params["name_template"] = "{stream_name}"
            if_exists = self.params.get("if_exists", "skip")
            if if_exists not in ("skip", "merge", "update"):
                errors.append(f"create_channel.if_exists must be 'skip', 'merge', or 'update'")
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

            match_by = self.params.get("match_by", "tvg_id")
            if match_by not in ("tvg_id", "normalized_name", "stream_group"):
                errors.append(f"merge_streams.match_by must be 'tvg_id', 'normalized_name', or 'stream_group'")

            if target == "existing_channel":
                if not self.params.get("find_channel_by"):
                    errors.append("merge_streams with target='existing_channel' requires 'find_channel_by'")
                elif self.params["find_channel_by"] not in ("name_exact", "name_regex", "tvg_id"):
                    errors.append("merge_streams.find_channel_by must be 'name_exact', 'name_regex', or 'tvg_id'")

        # Validate assign_logo
        elif action_type == ActionType.ASSIGN_LOGO:
            value = self.params.get("value")
            if value is None:
                errors.append("assign_logo requires a 'value' (URL or 'from_stream')")

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

        # Validate assign_profile
        elif action_type == ActionType.ASSIGN_PROFILE:
            profile_id = self.params.get("profile_id")
            if profile_id is None or not isinstance(profile_id, int):
                errors.append("assign_profile requires a 'profile_id' (integer)")

        # Validate set_channel_number
        elif action_type == ActionType.SET_CHANNEL_NUMBER:
            value = self.params.get("value")
            if value is None:
                errors.append("set_channel_number requires a 'value' ('auto', integer, or 'min-max' range)")

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
                        try:
                            re.compile(pattern)
                        except re.error as e:
                            errors.append(f"Invalid regex pattern for set_variable: {e}")
                    if mode == "regex_replace":
                        replacement = self.params.get("replacement")
                        if replacement is None or not isinstance(replacement, str):
                            errors.append("set_variable with mode 'regex_replace' requires a 'replacement'")
                elif mode == "literal":
                    template = self.params.get("template")
                    if not template or not isinstance(template, str):
                        errors.append("set_variable with mode 'literal' requires a 'template'")

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
                try:
                    re.compile(pattern)
                except re.error as e:
                    errors.append(f"Invalid name_transform_pattern: {e}")
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
        return result.strip()


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

    return {
        "valid": len(errors) == 0,
        "errors": errors
    }


def parse_conditions(conditions_json: str) -> list[Condition]:
    """Parse JSON string into list of Condition objects."""
    try:
        data = json.loads(conditions_json) if isinstance(conditions_json, str) else conditions_json
        return [Condition.from_dict(c) for c in data]
    except (json.JSONDecodeError, TypeError, KeyError) as e:
        raise ValueError(f"Invalid conditions JSON: {e}")


def parse_actions(actions_json: str) -> list[Action]:
    """Parse JSON string into list of Action objects."""
    try:
        data = json.loads(actions_json) if isinstance(actions_json, str) else actions_json
        return [Action.from_dict(a) for a in data]
    except (json.JSONDecodeError, TypeError, KeyError) as e:
        raise ValueError(f"Invalid actions JSON: {e}")


# =============================================================================
# YAML Schema Support
# =============================================================================

def conditions_to_yaml_friendly(conditions: list) -> list:
    """
    Convert conditions to a YAML-friendly format.
    Simplifies single-key conditions to direct key-value pairs.
    """
    result = []
    for cond in conditions:
        if isinstance(cond, Condition):
            cond = cond.to_dict()

        cond_type = cond.get("type")

        # For simple conditions, use shorthand: {type: value}
        if cond_type not in ("and", "or", "not") and "conditions" not in cond:
            simple = {cond_type: cond.get("value")}
            if cond.get("case_sensitive"):
                simple["case_sensitive"] = True
            if cond.get("negate"):
                simple["negate"] = True
            result.append(simple)
        else:
            # For compound conditions, use full format
            result.append(cond)

    return result


def actions_to_yaml_friendly(actions: list) -> list:
    """
    Convert actions to a YAML-friendly format.
    """
    result = []
    for action in actions:
        if isinstance(action, Action):
            result.append(action.to_dict())
        else:
            result.append(action)
    return result
