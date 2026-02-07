"""
Unit tests for the auto_creation_schema module.

Tests condition/action validation, parsing, and template expansion.
"""
import pytest
import json

from auto_creation_schema import (
    Condition,
    ConditionType,
    Action,
    ActionType,
    TemplateVariables,
    validate_rule,
    parse_conditions,
    parse_actions,
)


class TestConditionFromDict:
    """Tests for Condition.from_dict()."""

    def test_simple_condition(self):
        """Parses a simple condition."""
        data = {"type": "stream_name_contains", "value": "Sports"}
        cond = Condition.from_dict(data)
        assert cond.type == "stream_name_contains"
        assert cond.value == "Sports"

    def test_condition_with_case_sensitive(self):
        """Parses condition with case_sensitive flag."""
        data = {"type": "stream_name_matches", "value": "ESPN.*", "case_sensitive": True}
        cond = Condition.from_dict(data)
        assert cond.case_sensitive is True

    def test_condition_with_negate(self):
        """Parses condition with negate flag."""
        data = {"type": "has_channel", "value": True, "negate": True}
        cond = Condition.from_dict(data)
        assert cond.negate is True

    def test_compound_and_condition(self):
        """Parses AND compound condition."""
        data = {
            "type": "and",
            "conditions": [
                {"type": "stream_name_contains", "value": "HD"},
                {"type": "quality_min", "value": 720}
            ]
        }
        cond = Condition.from_dict(data)
        assert cond.type == "and"
        assert len(cond.conditions) == 2
        assert cond.conditions[0].type == "stream_name_contains"
        assert cond.conditions[1].type == "quality_min"

    def test_nested_compound_conditions(self):
        """Parses nested compound conditions."""
        data = {
            "type": "and",
            "conditions": [
                {"type": "stream_name_contains", "value": "ESPN"},
                {
                    "type": "or",
                    "conditions": [
                        {"type": "quality_min", "value": 720},
                        {"type": "stream_name_contains", "value": "HD"}
                    ]
                }
            ]
        }
        cond = Condition.from_dict(data)
        assert cond.type == "and"
        assert cond.conditions[1].type == "or"
        assert len(cond.conditions[1].conditions) == 2

    def test_idempotent_from_dict(self):
        """from_dict returns same object if already a Condition."""
        original = Condition(type="always")
        result = Condition.from_dict(original)
        assert result is original


class TestConditionToDict:
    """Tests for Condition.to_dict()."""

    def test_simple_condition_to_dict(self):
        """Converts simple condition to dict."""
        cond = Condition(type="stream_name_contains", value="Sports")
        result = cond.to_dict()
        assert result == {"type": "stream_name_contains", "value": "Sports"}

    def test_condition_with_flags_to_dict(self):
        """Converts condition with flags to dict."""
        cond = Condition(type="stream_name_matches", value=".*HD$", case_sensitive=True, negate=True)
        result = cond.to_dict()
        assert result["case_sensitive"] is True
        assert result["negate"] is True

    def test_compound_condition_to_dict(self):
        """Converts compound condition to dict."""
        cond = Condition(
            type="and",
            conditions=[
                Condition(type="has_channel", value=False),
                Condition(type="quality_min", value=720)
            ]
        )
        result = cond.to_dict()
        assert result["type"] == "and"
        assert len(result["conditions"]) == 2


class TestConditionValidation:
    """Tests for Condition.validate()."""

    def test_valid_stream_name_contains(self):
        """Validates stream_name_contains condition."""
        cond = Condition(type="stream_name_contains", value="Sports")
        errors = cond.validate()
        assert len(errors) == 0

    def test_invalid_stream_name_contains_no_value(self):
        """Rejects stream_name_contains without value."""
        cond = Condition(type="stream_name_contains", value=None)
        errors = cond.validate()
        assert len(errors) > 0
        assert "requires a string" in errors[0]

    def test_valid_regex_pattern(self):
        """Validates valid regex pattern."""
        cond = Condition(type="stream_name_matches", value="^ESPN.*HD$")
        errors = cond.validate()
        assert len(errors) == 0

    def test_invalid_regex_pattern(self):
        """Rejects invalid regex pattern."""
        cond = Condition(type="stream_name_matches", value="[invalid(")
        errors = cond.validate()
        assert len(errors) > 0
        assert "Invalid regex" in errors[0]

    def test_valid_quality_min(self):
        """Validates quality_min condition."""
        cond = Condition(type="quality_min", value=720)
        errors = cond.validate()
        assert len(errors) == 0

    def test_invalid_quality_min_negative(self):
        """Rejects negative quality_min."""
        cond = Condition(type="quality_min", value=-100)
        errors = cond.validate()
        assert len(errors) > 0

    def test_valid_provider_is_single(self):
        """Validates provider_is with single value."""
        cond = Condition(type="provider_is", value=1)
        errors = cond.validate()
        assert len(errors) == 0

    def test_valid_provider_is_list(self):
        """Validates provider_is with list value."""
        cond = Condition(type="provider_is", value=[1, 2, 3])
        errors = cond.validate()
        assert len(errors) == 0

    def test_invalid_provider_is_string(self):
        """Rejects provider_is with string value."""
        cond = Condition(type="provider_is", value="provider1")
        errors = cond.validate()
        assert len(errors) > 0

    def test_valid_and_condition(self):
        """Validates AND with multiple sub-conditions."""
        cond = Condition(
            type="and",
            conditions=[
                Condition(type="has_channel", value=False),
                Condition(type="quality_min", value=720)
            ]
        )
        errors = cond.validate()
        assert len(errors) == 0

    def test_invalid_and_single_condition(self):
        """Rejects AND with single sub-condition."""
        cond = Condition(
            type="and",
            conditions=[Condition(type="has_channel", value=False)]
        )
        errors = cond.validate()
        assert len(errors) > 0
        assert "at least 2" in errors[0]

    def test_valid_not_condition(self):
        """Validates NOT with single sub-condition."""
        cond = Condition(
            type="not",
            conditions=[Condition(type="has_channel", value=True)]
        )
        errors = cond.validate()
        assert len(errors) == 0

    def test_invalid_not_multiple_conditions(self):
        """Rejects NOT with multiple sub-conditions."""
        cond = Condition(
            type="not",
            conditions=[
                Condition(type="has_channel", value=True),
                Condition(type="quality_min", value=720)
            ]
        )
        errors = cond.validate()
        assert len(errors) > 0
        assert "exactly 1" in errors[0]

    def test_unknown_condition_type(self):
        """Rejects unknown condition type."""
        cond = Condition(type="unknown_type", value="test")
        errors = cond.validate()
        assert len(errors) > 0
        assert "Unknown condition type" in errors[0]

    def test_always_condition(self):
        """Validates always condition."""
        cond = Condition(type="always")
        errors = cond.validate()
        assert len(errors) == 0

    def test_never_condition(self):
        """Validates never condition."""
        cond = Condition(type="never")
        errors = cond.validate()
        assert len(errors) == 0


class TestActionFromDict:
    """Tests for Action.from_dict()."""

    def test_simple_action(self):
        """Parses simple action."""
        data = {"type": "skip"}
        action = Action.from_dict(data)
        assert action.type == "skip"
        assert action.params == {}

    def test_action_with_params(self):
        """Parses action with parameters."""
        data = {
            "type": "create_channel",
            "name_template": "{stream_name}",
            "channel_number": "auto",
            "if_exists": "skip"
        }
        action = Action.from_dict(data)
        assert action.type == "create_channel"
        assert action.params["name_template"] == "{stream_name}"
        assert action.params["channel_number"] == "auto"

    def test_idempotent_from_dict(self):
        """from_dict returns same object if already an Action."""
        original = Action(type="skip")
        result = Action.from_dict(original)
        assert result is original


class TestActionToDict:
    """Tests for Action.to_dict()."""

    def test_simple_action_to_dict(self):
        """Converts simple action to dict."""
        action = Action(type="skip")
        result = action.to_dict()
        assert result == {"type": "skip"}

    def test_action_with_params_to_dict(self):
        """Converts action with params to dict."""
        action = Action(
            type="create_channel",
            params={"name_template": "{stream_name}", "if_exists": "merge"}
        )
        result = action.to_dict()
        assert result["type"] == "create_channel"
        assert result["name_template"] == "{stream_name}"
        assert result["if_exists"] == "merge"


class TestActionValidation:
    """Tests for Action.validate()."""

    def test_valid_skip(self):
        """Validates skip action."""
        action = Action(type="skip")
        errors = action.validate()
        assert len(errors) == 0

    def test_valid_stop_processing(self):
        """Validates stop_processing action."""
        action = Action(type="stop_processing")
        errors = action.validate()
        assert len(errors) == 0

    def test_valid_create_channel(self):
        """Validates create_channel action."""
        action = Action(
            type="create_channel",
            params={"name_template": "{stream_name}", "if_exists": "skip"}
        )
        errors = action.validate()
        assert len(errors) == 0

    def test_invalid_create_channel_if_exists(self):
        """Rejects invalid if_exists value for create_channel."""
        action = Action(
            type="create_channel",
            params={"if_exists": "invalid"}
        )
        errors = action.validate()
        assert len(errors) > 0
        assert "if_exists" in errors[0]

    def test_valid_create_group(self):
        """Validates create_group action."""
        action = Action(
            type="create_group",
            params={"name_template": "{stream_group}"}
        )
        errors = action.validate()
        assert len(errors) == 0

    def test_valid_merge_streams(self):
        """Validates merge_streams action."""
        action = Action(
            type="merge_streams",
            params={"target": "auto", "match_by": "tvg_id"}
        )
        errors = action.validate()
        assert len(errors) == 0

    def test_invalid_merge_streams_target(self):
        """Rejects invalid target for merge_streams."""
        action = Action(
            type="merge_streams",
            params={"target": "invalid"}
        )
        errors = action.validate()
        assert len(errors) > 0

    def test_merge_streams_existing_channel_requires_find_by(self):
        """Rejects existing_channel target without find_channel_by."""
        action = Action(
            type="merge_streams",
            params={"target": "existing_channel"}
        )
        errors = action.validate()
        assert len(errors) > 0
        assert "find_channel_by" in errors[0]

    def test_valid_assign_logo(self):
        """Validates assign_logo action."""
        action = Action(
            type="assign_logo",
            params={"value": "from_stream"}
        )
        errors = action.validate()
        assert len(errors) == 0

    def test_invalid_assign_logo_no_value(self):
        """Rejects assign_logo without value."""
        action = Action(type="assign_logo", params={})
        errors = action.validate()
        assert len(errors) > 0
        assert "value" in errors[0]

    def test_valid_log_match(self):
        """Validates log_match action."""
        action = Action(
            type="log_match",
            params={"message": "Matched stream {stream_name}"}
        )
        errors = action.validate()
        assert len(errors) == 0

    def test_invalid_log_match_no_message(self):
        """Rejects log_match without message."""
        action = Action(type="log_match", params={})
        errors = action.validate()
        assert len(errors) > 0
        assert "message" in errors[0]

    def test_unknown_action_type(self):
        """Rejects unknown action type."""
        action = Action(type="unknown_action")
        errors = action.validate()
        assert len(errors) > 0
        assert "Unknown action type" in errors[0]


class TestTemplateVariables:
    """Tests for TemplateVariables."""

    def test_expand_simple_template(self):
        """Expands simple template."""
        template = "{stream_name}"
        context = {"stream_name": "ESPN HD"}
        result = TemplateVariables.expand_template(template, context)
        assert result == "ESPN HD"

    def test_expand_multiple_variables(self):
        """Expands template with multiple variables."""
        template = "{stream_name} - {quality}"
        context = {"stream_name": "ESPN", "quality": "1080p"}
        result = TemplateVariables.expand_template(template, context)
        assert result == "ESPN - 1080p"

    def test_expand_missing_variable(self):
        """Handles missing variable by keeping placeholder."""
        template = "{stream_name} ({missing})"
        context = {"stream_name": "ESPN"}
        result = TemplateVariables.expand_template(template, context)
        # Missing variables are kept as placeholders
        assert result == "ESPN ({missing})"

    def test_expand_none_value(self):
        """Handles None value in context."""
        template = "{stream_name} - {tvg_id}"
        context = {"stream_name": "ESPN", "tvg_id": None}
        result = TemplateVariables.expand_template(template, context)
        assert result == "ESPN -"

    def test_expand_strips_whitespace(self):
        """Strips leading/trailing whitespace from result."""
        template = "  {stream_name}  "
        context = {"stream_name": "ESPN"}
        result = TemplateVariables.expand_template(template, context)
        assert result == "ESPN"

    def test_all_variables_list(self):
        """Returns all available variables."""
        variables = TemplateVariables.all_variables()
        assert "stream_name" in variables
        assert "stream_group" in variables
        assert "quality" in variables
        assert "tvg_id" in variables


class TestValidateRule:
    """Tests for validate_rule()."""

    def test_valid_rule(self):
        """Validates a complete valid rule."""
        conditions = [
            {"type": "stream_name_contains", "value": "Sports"}
        ]
        actions = [
            {"type": "create_channel", "name_template": "{stream_name}"}
        ]
        result = validate_rule(conditions, actions)
        assert result["valid"] is True
        assert len(result["errors"]) == 0

    def test_rule_without_conditions(self):
        """Rejects rule without conditions."""
        result = validate_rule([], [{"type": "skip"}])
        assert result["valid"] is False
        assert "at least one condition" in result["errors"][0]

    def test_rule_without_actions(self):
        """Rejects rule without actions."""
        result = validate_rule(
            [{"type": "always"}],
            []
        )
        assert result["valid"] is False
        assert "at least one action" in result["errors"][0]

    def test_rule_with_invalid_condition(self):
        """Reports condition errors."""
        conditions = [
            {"type": "stream_name_matches", "value": "[invalid("}
        ]
        actions = [{"type": "skip"}]
        result = validate_rule(conditions, actions)
        assert result["valid"] is False
        assert "conditions[0]" in result["errors"][0]

    def test_rule_with_invalid_action(self):
        """Reports action errors."""
        conditions = [{"type": "always"}]
        actions = [
            {"type": "merge_streams", "target": "invalid"}
        ]
        result = validate_rule(conditions, actions)
        assert result["valid"] is False
        assert "actions[0]" in result["errors"][0]


class TestParseConditions:
    """Tests for parse_conditions()."""

    def test_parse_json_string(self):
        """Parses JSON string to conditions."""
        json_str = '[{"type": "always"}]'
        conditions = parse_conditions(json_str)
        assert len(conditions) == 1
        assert conditions[0].type == "always"

    def test_parse_list(self):
        """Parses list directly."""
        data = [{"type": "has_channel", "value": False}]
        conditions = parse_conditions(data)
        assert len(conditions) == 1
        assert conditions[0].type == "has_channel"

    def test_parse_invalid_json(self):
        """Raises error for invalid JSON."""
        with pytest.raises(ValueError):
            parse_conditions("not valid json")


class TestParseActions:
    """Tests for parse_actions()."""

    def test_parse_json_string(self):
        """Parses JSON string to actions."""
        json_str = '[{"type": "skip"}]'
        actions = parse_actions(json_str)
        assert len(actions) == 1
        assert actions[0].type == "skip"

    def test_parse_list(self):
        """Parses list directly."""
        data = [{"type": "create_channel", "name_template": "{stream_name}"}]
        actions = parse_actions(data)
        assert len(actions) == 1
        assert actions[0].type == "create_channel"

    def test_parse_invalid_json(self):
        """Raises error for invalid JSON."""
        with pytest.raises(ValueError):
            parse_actions("not valid json")


class TestConditionTypes:
    """Tests for ConditionType enum."""

    def test_all_condition_types_exist(self):
        """All expected condition types exist."""
        assert ConditionType.STREAM_NAME_MATCHES.value == "stream_name_matches"
        assert ConditionType.STREAM_NAME_CONTAINS.value == "stream_name_contains"
        assert ConditionType.STREAM_GROUP_MATCHES.value == "stream_group_matches"
        assert ConditionType.TVG_ID_EXISTS.value == "tvg_id_exists"
        assert ConditionType.HAS_CHANNEL.value == "has_channel"
        assert ConditionType.QUALITY_MIN.value == "quality_min"
        assert ConditionType.QUALITY_MAX.value == "quality_max"
        assert ConditionType.AND.value == "and"
        assert ConditionType.OR.value == "or"
        assert ConditionType.NOT.value == "not"


class TestNameTransformValidation:
    """Tests for name_transform_pattern/name_transform_replacement validation."""

    def test_valid_name_transform(self):
        """Validates create_channel with valid name transform."""
        action = Action(
            type="create_channel",
            params={
                "name_template": "{stream_name}",
                "name_transform_pattern": r"^US:\s*",
                "name_transform_replacement": ""
            }
        )
        errors = action.validate()
        assert len(errors) == 0

    def test_invalid_name_transform_regex(self):
        """Rejects invalid regex in name_transform_pattern."""
        action = Action(
            type="create_channel",
            params={
                "name_template": "{stream_name}",
                "name_transform_pattern": "[invalid("
            }
        )
        errors = action.validate()
        assert len(errors) > 0
        assert "name_transform_pattern" in errors[0]

    def test_name_transform_on_create_group(self):
        """Validates create_group with valid name transform."""
        action = Action(
            type="create_group",
            params={
                "name_template": "{stream_group}",
                "name_transform_pattern": r"\s+\(.*\)$",
                "name_transform_replacement": ""
            }
        )
        errors = action.validate()
        assert len(errors) == 0

    def test_name_transform_pattern_not_string(self):
        """Rejects non-string name_transform_pattern."""
        action = Action(
            type="create_channel",
            params={
                "name_template": "{stream_name}",
                "name_transform_pattern": 123
            }
        )
        errors = action.validate()
        assert len(errors) > 0
        assert "must be a string" in errors[0]

    def test_name_transform_replacement_not_string(self):
        """Rejects non-string name_transform_replacement."""
        action = Action(
            type="create_channel",
            params={
                "name_template": "{stream_name}",
                "name_transform_pattern": "^US:",
                "name_transform_replacement": 123
            }
        )
        errors = action.validate()
        assert len(errors) > 0
        assert "name_transform_replacement" in errors[0]

    def test_no_name_transform_is_valid(self):
        """create_channel without name transform is valid."""
        action = Action(
            type="create_channel",
            params={"name_template": "{stream_name}"}
        )
        errors = action.validate()
        assert len(errors) == 0


class TestSetVariableValidation:
    """Tests for set_variable action validation."""

    def test_valid_regex_extract(self):
        """Validates set_variable with regex_extract mode."""
        action = Action(
            type="set_variable",
            params={
                "variable_name": "region",
                "variable_mode": "regex_extract",
                "source_field": "stream_name",
                "pattern": r"^(\w+):"
            }
        )
        errors = action.validate()
        assert len(errors) == 0

    def test_valid_regex_replace(self):
        """Validates set_variable with regex_replace mode."""
        action = Action(
            type="set_variable",
            params={
                "variable_name": "clean_name",
                "variable_mode": "regex_replace",
                "source_field": "stream_name",
                "pattern": r"^US:\s*",
                "replacement": ""
            }
        )
        errors = action.validate()
        assert len(errors) == 0

    def test_valid_literal(self):
        """Validates set_variable with literal mode."""
        action = Action(
            type="set_variable",
            params={
                "variable_name": "channel_prefix",
                "variable_mode": "literal",
                "template": "Channel {var:region}"
            }
        )
        errors = action.validate()
        assert len(errors) == 0

    def test_missing_variable_name(self):
        """Rejects set_variable without variable_name."""
        action = Action(
            type="set_variable",
            params={
                "variable_mode": "literal",
                "template": "test"
            }
        )
        errors = action.validate()
        assert len(errors) > 0
        assert "variable_name" in errors[0]

    def test_invalid_variable_name(self):
        """Rejects set_variable with invalid variable_name."""
        action = Action(
            type="set_variable",
            params={
                "variable_name": "invalid-name",
                "variable_mode": "literal",
                "template": "test"
            }
        )
        errors = action.validate()
        assert len(errors) > 0
        assert "alphanumeric" in errors[0]

    def test_invalid_variable_mode(self):
        """Rejects set_variable with unknown mode."""
        action = Action(
            type="set_variable",
            params={
                "variable_name": "test",
                "variable_mode": "unknown"
            }
        )
        errors = action.validate()
        assert len(errors) > 0
        assert "variable_mode" in errors[0]

    def test_regex_extract_missing_source(self):
        """Rejects regex_extract without source_field."""
        action = Action(
            type="set_variable",
            params={
                "variable_name": "test",
                "variable_mode": "regex_extract",
                "pattern": ".*"
            }
        )
        errors = action.validate()
        assert len(errors) > 0
        assert "source_field" in errors[0]

    def test_regex_extract_missing_pattern(self):
        """Rejects regex_extract without pattern."""
        action = Action(
            type="set_variable",
            params={
                "variable_name": "test",
                "variable_mode": "regex_extract",
                "source_field": "stream_name"
            }
        )
        errors = action.validate()
        assert len(errors) > 0
        assert "pattern" in errors[0]

    def test_regex_extract_invalid_pattern(self):
        """Rejects set_variable with invalid regex."""
        action = Action(
            type="set_variable",
            params={
                "variable_name": "test",
                "variable_mode": "regex_extract",
                "source_field": "stream_name",
                "pattern": "[invalid("
            }
        )
        errors = action.validate()
        assert len(errors) > 0
        assert "Invalid regex" in errors[0]

    def test_regex_replace_missing_replacement(self):
        """Rejects regex_replace without replacement."""
        action = Action(
            type="set_variable",
            params={
                "variable_name": "test",
                "variable_mode": "regex_replace",
                "source_field": "stream_name",
                "pattern": ".*"
            }
        )
        errors = action.validate()
        assert len(errors) > 0
        assert "replacement" in errors[0]

    def test_literal_missing_template(self):
        """Rejects literal without template."""
        action = Action(
            type="set_variable",
            params={
                "variable_name": "test",
                "variable_mode": "literal"
            }
        )
        errors = action.validate()
        assert len(errors) > 0
        assert "template" in errors[0]


class TestTemplateExpandWithCustomVariables:
    """Tests for TemplateVariables.expand_template with custom variables."""

    def test_expand_custom_variable(self):
        """Expands {var:name} custom variables."""
        template = "Channel {var:region}"
        context = {"stream_name": "ESPN"}
        custom = {"region": "US"}
        result = TemplateVariables.expand_template(template, context, custom)
        assert result == "Channel US"

    def test_expand_mixed_variables(self):
        """Expands both standard and custom variables."""
        template = "{stream_name} - {var:suffix}"
        context = {"stream_name": "ESPN"}
        custom = {"suffix": "HD"}
        result = TemplateVariables.expand_template(template, context, custom)
        assert result == "ESPN - HD"

    def test_expand_no_custom_variables(self):
        """Works without custom variables."""
        template = "{stream_name}"
        context = {"stream_name": "ESPN"}
        result = TemplateVariables.expand_template(template, context)
        assert result == "ESPN"

    def test_expand_empty_custom_variable(self):
        """Handles empty custom variable value."""
        template = "Channel {var:region}"
        context = {}
        custom = {"region": ""}
        result = TemplateVariables.expand_template(template, context, custom)
        assert result == "Channel"

    def test_expand_missing_custom_variable_kept(self):
        """Missing custom variables kept as placeholders."""
        template = "Channel {var:unknown}"
        context = {}
        result = TemplateVariables.expand_template(template, context)
        assert result == "Channel {var:unknown}"


class TestActionTypes:
    """Tests for ActionType enum."""

    def test_all_action_types_exist(self):
        """All expected action types exist."""
        assert ActionType.CREATE_CHANNEL.value == "create_channel"
        assert ActionType.CREATE_GROUP.value == "create_group"
        assert ActionType.MERGE_STREAMS.value == "merge_streams"
        assert ActionType.ASSIGN_LOGO.value == "assign_logo"
        assert ActionType.ASSIGN_TVG_ID.value == "assign_tvg_id"
        assert ActionType.SET_VARIABLE.value == "set_variable"
        assert ActionType.SKIP.value == "skip"
        assert ActionType.STOP_PROCESSING.value == "stop_processing"
