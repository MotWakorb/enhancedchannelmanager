"""TDD tests for enhancedchannelmanager-e5n8j — normalization CRUD tools.

Covers create/update/delete_normalization_group, create/update/
delete_normalization_rule, and apply_normalization_to_channels. Destructive
ops (group delete cascades to rules; apply-to-channels mutates channels in
bulk) follow the confirm-gated / preview-first conventions used elsewhere
(delete_channel, delete_channel_pipeline_rule).
"""
import pytest
from unittest.mock import AsyncMock, patch


def _mcp():
    from mcp.server.fastmcp import FastMCP
    from tools.normalization import register

    mcp = FastMCP("test")
    register(mcp)
    return mcp


def _text(result) -> str:
    return result[0][0].text


# ---------------------------------------------------------------------------
# Groups
# ---------------------------------------------------------------------------
class TestCreateNormalizationGroup:
    @pytest.mark.asyncio
    async def test_creates_with_defaults(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"id": 1, "name": "Sports"}

        with patch("tools.normalization.get_ecm_client", return_value=client):
            result = await mcp.call_tool("create_normalization_group", {"name": "Sports"})

        body = client.call_endpoint.call_args.kwargs["body"]
        assert body == {"name": "Sports", "enabled": True, "priority": 0}
        assert "id=1" in _text(result)

    @pytest.mark.asyncio
    async def test_creates_with_full_fields(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"id": 2, "name": "News"}

        with patch("tools.normalization.get_ecm_client", return_value=client):
            await mcp.call_tool("create_normalization_group", {
                "name": "News", "description": "News channels", "enabled": False, "priority": 5,
            })

        body = client.call_endpoint.call_args.kwargs["body"]
        assert body == {
            "name": "News", "description": "News channels", "enabled": False, "priority": 5,
        }


class TestUpdateNormalizationGroup:
    @pytest.mark.asyncio
    async def test_forwards_only_provided_fields(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"name": "Sports"}

        with patch("tools.normalization.get_ecm_client", return_value=client):
            await mcp.call_tool("update_normalization_group", {"group_id": 1, "enabled": False})

        body = client.call_endpoint.call_args.kwargs["body"]
        assert body == {"enabled": False}

    @pytest.mark.asyncio
    async def test_no_changes_short_circuits(self):
        mcp = _mcp()
        client = AsyncMock()

        with patch("tools.normalization.get_ecm_client", return_value=client):
            result = await mcp.call_tool("update_normalization_group", {"group_id": 1})

        assert "No changes specified" in _text(result)
        client.call_endpoint.assert_not_called()


class TestDeleteNormalizationGroup:
    @pytest.mark.asyncio
    async def test_preview_on_confirm_false_deletes_nothing(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {
            "id": 1, "name": "Sports", "rules": [{"id": 10}, {"id": 11}],
        }

        with patch("tools.normalization.get_ecm_client", return_value=client):
            result = await mcp.call_tool("delete_normalization_group", {"group_id": 1})

        text = _text(result)
        assert "2 rule" in text
        assert "confirm=True" in text
        # Only the preview GET happened — no delete call.
        called_endpoints = [c.args[0].name for c in client.call_endpoint.call_args_list]
        assert called_endpoints == ["normalization_get_group"]

    @pytest.mark.asyncio
    async def test_confirm_true_deletes_and_reports_cleaned_rules(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"status": "deleted", "id": 1, "rules_cleaned": 3}

        with patch("tools.normalization.get_ecm_client", return_value=client):
            result = await mcp.call_tool("delete_normalization_group", {"group_id": 1, "confirm": True})

        text = _text(result)
        assert "deleted" in text.lower()
        assert "3" in text
        called_endpoints = [c.args[0].name for c in client.call_endpoint.call_args_list]
        assert called_endpoints == ["normalization_delete_group"]


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------
class TestCreateNormalizationRule:
    @pytest.mark.asyncio
    async def test_creates_with_legacy_condition(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"id": 100, "name": "Strip NFL"}

        with patch("tools.normalization.get_ecm_client", return_value=client):
            result = await mcp.call_tool("create_normalization_rule", {
                "group_id": 1, "name": "Strip NFL",
                "condition_type": "contains", "condition_value": "NFL:",
                "action_type": "strip_prefix", "action_value": "NFL:",
            })

        body = client.call_endpoint.call_args.kwargs["body"]
        assert body["group_id"] == 1
        assert body["condition_type"] == "contains"
        assert body["condition_value"] == "NFL:"
        assert body["action_type"] == "strip_prefix"
        assert body["action_value"] == "NFL:"
        assert "id=100" in _text(result)

    @pytest.mark.asyncio
    async def test_creates_with_compound_conditions(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"id": 101, "name": "Compound"}

        conditions = [{"type": "contains", "value": "HD", "negate": False}]
        with patch("tools.normalization.get_ecm_client", return_value=client):
            await mcp.call_tool("create_normalization_rule", {
                "group_id": 1, "name": "Compound", "action_type": "remove",
                "conditions": conditions, "condition_logic": "OR",
            })

        body = client.call_endpoint.call_args.kwargs["body"]
        assert body["conditions"] == conditions
        assert body["condition_logic"] == "OR"
        # Legacy condition fields omitted when not given.
        assert "condition_type" not in body
        assert "condition_value" not in body

    @pytest.mark.asyncio
    async def test_omits_optional_fields_when_not_given(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"id": 102, "name": "Minimal"}

        with patch("tools.normalization.get_ecm_client", return_value=client):
            await mcp.call_tool("create_normalization_rule", {
                "group_id": 1, "name": "Minimal", "action_type": "remove",
            })

        body = client.call_endpoint.call_args.kwargs["body"]
        for absent in (
            "description", "condition_type", "condition_value", "tag_group_id",
            "tag_match_position", "conditions", "action_value",
            "else_action_type", "else_action_value",
        ):
            assert absent not in body, f"{absent} should be omitted when not given"
        # Always-present-with-default fields.
        assert body["enabled"] is True
        assert body["priority"] == 0
        assert body["case_sensitive"] is False
        assert body["condition_logic"] == "AND"
        assert body["stop_processing"] is False


class TestUpdateNormalizationRule:
    @pytest.mark.asyncio
    async def test_forwards_only_provided_fields(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"name": "Rule"}

        with patch("tools.normalization.get_ecm_client", return_value=client):
            await mcp.call_tool("update_normalization_rule", {
                "rule_id": 100, "enabled": False, "action_value": "new-value",
            })

        body = client.call_endpoint.call_args.kwargs["body"]
        assert body == {"enabled": False, "action_value": "new-value"}

    @pytest.mark.asyncio
    async def test_no_changes_short_circuits(self):
        mcp = _mcp()
        client = AsyncMock()

        with patch("tools.normalization.get_ecm_client", return_value=client):
            result = await mcp.call_tool("update_normalization_rule", {"rule_id": 100})

        assert "No changes specified" in _text(result)
        client.call_endpoint.assert_not_called()


class TestDeleteNormalizationRule:
    @pytest.mark.asyncio
    async def test_preview_on_confirm_false_deletes_nothing(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"id": 100, "name": "Strip NFL"}

        with patch("tools.normalization.get_ecm_client", return_value=client):
            result = await mcp.call_tool("delete_normalization_rule", {"rule_id": 100})

        text = _text(result)
        assert "Strip NFL" in text
        assert "confirm=True" in text
        called_endpoints = [c.args[0].name for c in client.call_endpoint.call_args_list]
        assert called_endpoints == ["normalization_get_rule"]

    @pytest.mark.asyncio
    async def test_confirm_true_deletes(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"status": "deleted", "id": 100}

        with patch("tools.normalization.get_ecm_client", return_value=client):
            result = await mcp.call_tool("delete_normalization_rule", {"rule_id": 100, "confirm": True})

        assert "deleted" in _text(result).lower()
        called_endpoints = [c.args[0].name for c in client.call_endpoint.call_args_list]
        assert called_endpoints == ["normalization_delete_rule"]


# ---------------------------------------------------------------------------
# apply_normalization_to_channels
# ---------------------------------------------------------------------------
class TestApplyNormalizationToChannels:
    @pytest.mark.asyncio
    async def test_defaults_to_dry_run_and_makes_no_body(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {
            "dry_run": True,
            "diffs": [{"channel_id": 1, "current_name": "◉ ESPN", "proposed_name": "ESPN"}],
            "channels_with_changes": 1,
        }

        with patch("tools.normalization.get_ecm_client", return_value=client):
            result = await mcp.call_tool("apply_normalization_to_channels", {})

        call = client.call_endpoint.call_args
        assert call.kwargs["query"] == {"dry_run": True}
        assert call.kwargs["body"] is None
        text = _text(result)
        assert "Preview" in text
        assert "◉ ESPN" in text
        assert "dry_run=False" in text

    @pytest.mark.asyncio
    async def test_no_changes_dry_run_reports_clean(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"dry_run": True, "diffs": [], "channels_with_changes": 0}

        with patch("tools.normalization.get_ecm_client", return_value=client):
            result = await mcp.call_tool("apply_normalization_to_channels", {})

        assert "No channels would change" in _text(result)

    @pytest.mark.asyncio
    async def test_collision_flagged_in_preview(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {
            "dry_run": True,
            "diffs": [{
                "channel_id": 1, "current_name": "◉ ESPN", "proposed_name": "ESPN",
                "collision": True, "collision_target_id": 2,
            }],
            "channels_with_changes": 1,
        }

        with patch("tools.normalization.get_ecm_client", return_value=client):
            result = await mcp.call_tool("apply_normalization_to_channels", {})

        assert "COLLISION" in _text(result)

    @pytest.mark.asyncio
    async def test_execute_mode_sends_actions_in_body(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {
            "dry_run": False, "status": "completed",
            "renamed": [{"channel_id": 1, "old_name": "◉ ESPN", "new_name": "ESPN"}],
            "merged": [], "skipped": [], "errors": [],
        }

        actions = [{"channel_id": 1, "action": "rename"}]
        with patch("tools.normalization.get_ecm_client", return_value=client):
            result = await mcp.call_tool("apply_normalization_to_channels", {
                "dry_run": False, "actions": actions,
            })

        call = client.call_endpoint.call_args
        assert call.kwargs["query"] == {"dry_run": False}
        assert call.kwargs["body"] == {"actions": actions}
        text = _text(result)
        assert "1 renamed" in text

    @pytest.mark.asyncio
    async def test_execute_mode_with_no_actions_sends_no_body(self):
        """Safe default: an execute call with nothing to act on changes nothing."""
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {
            "dry_run": False, "status": "completed",
            "renamed": [], "merged": [], "skipped": [{"channel_id": 1, "reason": "no action specified"}],
            "errors": [],
        }

        with patch("tools.normalization.get_ecm_client", return_value=client):
            result = await mcp.call_tool("apply_normalization_to_channels", {"dry_run": False})

        call = client.call_endpoint.call_args
        assert call.kwargs["body"] is None
        assert "0 renamed" in _text(result)
