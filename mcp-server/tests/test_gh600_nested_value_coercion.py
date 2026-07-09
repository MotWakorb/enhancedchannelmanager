"""Tests for GH #600 — lenient coercion of stringified values nested in
``conditions``/``actions`` (bd-3aczd).

Top-level tool params are typed, so FastMCP/Pydantic lax-coerces a stringified
"true" back to True. Values nested inside the ``list[dict]`` params are untyped
and used to ride through verbatim — an LLM MCP client that stringifies nested
scalars ("true", "0.75", "[912, 913]") got a backend 400
(merge_streams.loose_name_match must be a boolean, ...) or, worse, a silently
inverted condition (truthy "false" for ``negate``). The MCP tool boundary now
coerces the known typed fields; everything else passes through unchanged.
"""
import pytest
from unittest.mock import AsyncMock, patch

from tools.channel_pipeline import (
    _coerce_actions,
    _coerce_conditions,
)


def _make_mock(return_value=None):
    mock = AsyncMock()
    mock.call_endpoint.return_value = return_value if return_value is not None else {}
    return mock


def _register_and_get_mcp():
    from tools.channel_pipeline import register
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("test")
    register(mcp)
    return mcp


def _sent_payload(mock_client):
    """Return the body dict passed to the (single) call_endpoint invocation."""
    call = mock_client.call_endpoint.call_args
    return call.kwargs.get("body")


class TestCoerceActions:
    """Unit tests for the _coerce_actions helper."""

    def test_gh600_repro_stringified_scored_fuzzy_action(self):
        """The exact GH #600 failure shape: all four params stringified."""
        [coerced] = _coerce_actions([{
            "type": "merge_streams",
            "target": "auto",
            "loose_name_match": "true",
            "min_score": "0.75",
            "target_channel_in_group": "[912, 913]",
            "allow_no_callsign": "true",
        }])
        assert coerced["loose_name_match"] is True
        assert coerced["min_score"] == 0.75
        assert coerced["target_channel_in_group"] == [912, 913]
        assert coerced["allow_no_callsign"] is True

    def test_correctly_typed_values_untouched(self):
        action = {
            "type": "merge_streams",
            "target": "auto",
            "loose_name_match": True,
            "min_score": 0.75,
            "target_channel_in_group": [912, 913],
            "allow_no_callsign": False,
        }
        [coerced] = _coerce_actions([action])
        assert coerced == action

    def test_string_fields_never_coerced(self):
        """Field-aware: a numeric-looking name_template stays a string."""
        [coerced] = _coerce_actions([{
            "type": "create_channel",
            "name_template": "123",
            "if_exists": "merge",
        }])
        assert coerced["name_template"] == "123"
        assert coerced["if_exists"] == "merge"

    def test_bool_case_insensitive_and_stripped(self):
        [coerced] = _coerce_actions([{"type": "merge_streams", "loose_name_match": " True "}])
        assert coerced["loose_name_match"] is True

    def test_unparseable_values_pass_through_for_backend_error(self):
        """Garbage stays as-is so the backend's clear 400 still surfaces."""
        [coerced] = _coerce_actions([{
            "type": "merge_streams",
            "loose_name_match": "yes",
            "min_score": "high",
            "target_channel_in_group": "912 and 913",
        }])
        assert coerced["loose_name_match"] == "yes"
        assert coerced["min_score"] == "high"
        assert coerced["target_channel_in_group"] == "912 and 913"

    def test_int_list_with_string_elements(self):
        [coerced] = _coerce_actions([{
            "type": "merge_streams",
            "target_channel_in_group": ["912", "913"],
        }])
        assert coerced["target_channel_in_group"] == [912, 913]

    def test_bool_never_reinterpreted_as_group_id(self):
        """The backend rejects True as a group ID deliberately — keep that."""
        [coerced] = _coerce_actions([{
            "type": "merge_streams",
            "target_channel_in_group": [True, 913],
        }])
        assert coerced["target_channel_in_group"] == [True, 913]

    def test_stringified_ints(self):
        [coerced] = _coerce_actions([{
            "type": "assign_epg",
            "epg_id": "42",
            "set_tvg_id": "false",
        }])
        assert coerced["epg_id"] == 42
        assert coerced["set_tvg_id"] is False

    def test_min_score_integral_string_stays_valid(self):
        [coerced] = _coerce_actions([{"type": "merge_streams", "min_score": "1"}])
        assert coerced["min_score"] == 1

    def test_channel_profile_ids_json_string(self):
        [coerced] = _coerce_actions([{
            "type": "assign_channel_profile",
            "channel_profile_ids": "[1, 2, 3]",
        }])
        assert coerced["channel_profile_ids"] == [1, 2, 3]

    def test_input_dicts_not_mutated(self):
        action = {"type": "merge_streams", "loose_name_match": "true"}
        _coerce_actions([action])
        assert action["loose_name_match"] == "true"

    def test_non_list_and_non_dict_pass_through(self):
        assert _coerce_actions(None) is None
        assert _coerce_actions("not a list") == "not a list"
        assert _coerce_actions(["not a dict"]) == ["not a dict"]


class TestCoerceConditions:
    """Unit tests for the _coerce_conditions helper."""

    def test_negate_stringified_false_no_longer_truthy(self):
        """The nastiest failure: negate='false' is truthy and silently
        inverts the condition — no 400 warns about it."""
        [coerced] = _coerce_conditions([{
            "type": "stream_name_matches",
            "value": "(?i)test|demo",
            "negate": "false",
            "case_sensitive": "true",
        }])
        assert coerced["negate"] is False
        assert coerced["case_sensitive"] is True

    def test_string_value_types_untouched(self):
        """value is polymorphic — numeric-looking strings stay strings for
        string-valued condition types."""
        [coerced] = _coerce_conditions([{
            "type": "stream_name_contains",
            "value": "1080",
        }])
        assert coerced["value"] == "1080"

    def test_numeric_value_types_coerced(self):
        conds = _coerce_conditions([
            {"type": "quality_min", "value": "720"},
            {"type": "channel_in_group", "value": "912"},
            {"type": "has_channel", "value": "true"},
        ])
        assert conds[0]["value"] == 720
        assert conds[1]["value"] == 912
        assert conds[2]["value"] is True

    def test_provider_is_int_and_list_forms(self):
        conds = _coerce_conditions([
            {"type": "provider_is", "value": "3"},
            {"type": "provider_is", "value": "[3, 4]"},
            {"type": "provider_is", "value": ["3", "4"]},
        ])
        assert conds[0]["value"] == 3
        assert conds[1]["value"] == [3, 4]
        assert conds[2]["value"] == [3, 4]

    def test_nested_condition_groups_recursed(self):
        [coerced] = _coerce_conditions([{
            "operator": "or",
            "conditions": [
                {"type": "provider_is", "value": "3", "negate": "true"},
            ],
        }])
        inner = coerced["conditions"][0]
        assert inner["value"] == 3
        assert inner["negate"] is True


class TestCoercionThroughTools:
    """The coercion is applied on the create AND update tool paths."""

    @pytest.mark.asyncio
    async def test_create_coerces_stringified_action_values(self):
        mcp = _register_and_get_mcp()
        mock_client = _make_mock(return_value={"rule": {"id": 7}})

        with patch("tools.channel_pipeline.get_ecm_client", return_value=mock_client):
            await mcp.call_tool(
                "create_channel_pipeline_rule",
                {
                    "name": "GH600 repro",
                    "conditions": [{"type": "always"}],
                    "actions": [{
                        "type": "merge_streams",
                        "target": "auto",
                        "loose_name_match": "true",
                        "min_score": "0.75",
                        "target_channel_in_group": "[912, 913]",
                        "allow_no_callsign": "true",
                    }],
                },
            )

        sent = _sent_payload(mock_client)["actions"][0]
        assert sent["loose_name_match"] is True
        assert sent["min_score"] == 0.75
        assert sent["target_channel_in_group"] == [912, 913]
        assert sent["allow_no_callsign"] is True

    @pytest.mark.asyncio
    async def test_deprecated_alias_coerces_too(self):
        mcp = _register_and_get_mcp()
        mock_client = _make_mock(return_value={"rule": {"id": 8}})

        with patch("tools.channel_pipeline.get_ecm_client", return_value=mock_client):
            await mcp.call_tool(
                "create_auto_creation_rule",
                {
                    "name": "GH600 alias repro",
                    "conditions": [{"type": "stream_name_contains", "value": "Jackson Motorplex", "connector": "and"}],
                    "actions": [{"type": "merge_streams", "target": "auto", "loose_name_match": "true"}],
                },
            )

        sent = _sent_payload(mock_client)["actions"][0]
        assert sent["loose_name_match"] is True

    @pytest.mark.asyncio
    async def test_update_coerces_stringified_action_values(self):
        mcp = _register_and_get_mcp()
        mock_client = _make_mock(return_value={"rule": {"id": 9}})

        with patch("tools.channel_pipeline.get_ecm_client", return_value=mock_client):
            await mcp.call_tool(
                "update_channel_pipeline_rule",
                {
                    "rule_id": 9,
                    "actions": [{
                        "type": "merge_streams",
                        "target": "auto",
                        "loose_name_match": "true",
                        "min_score": "0.9",
                    }],
                    "conditions": [{"type": "quality_min", "value": "1080"}],
                },
            )

        payload = _sent_payload(mock_client)
        assert payload["actions"][0]["loose_name_match"] is True
        assert payload["actions"][0]["min_score"] == 0.9
        assert payload["conditions"][0]["value"] == 1080

    @pytest.mark.asyncio
    async def test_update_without_actions_still_works(self):
        mcp = _register_and_get_mcp()
        mock_client = _make_mock(return_value={"rule": {"id": 10}})

        with patch("tools.channel_pipeline.get_ecm_client", return_value=mock_client):
            await mcp.call_tool(
                "update_channel_pipeline_rule",
                {"rule_id": 10, "enabled": False},
            )

        payload = _sent_payload(mock_client)
        assert payload == {"enabled": False}
