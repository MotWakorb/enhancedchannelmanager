"""Stream name normalization tools."""
import logging

from mcp.server.fastmcp import FastMCP

from _endpoint_contracts import ENDPOINTS
from ecm_client import get_ecm_client

logger = logging.getLogger(__name__)


def register(mcp: FastMCP):
    @mcp.tool()
    async def test_normalization(text: str) -> str:
        """Test how stream names are normalized by running all enabled rules against the input.

        Args:
            text: The stream name to test normalization on (can pass multiple separated by commas)
        """
        try:
            client = get_ecm_client()
            texts = [t.strip() for t in text.split(",") if t.strip()]
            result = await client.call_endpoint(ENDPOINTS["normalization_test_batch"], body={"texts": texts})

            results = result.get("results", result) if isinstance(result, dict) else result

            if not results:
                return "No normalization results."

            if isinstance(results, list):
                lines = ["Normalization Results:"]
                for r in results:
                    if isinstance(r, dict):
                        orig = r.get("original", "?")
                        norm = r.get("normalized", r.get("result", "?"))
                        lines.append(f"  {orig} → {norm}")
                    else:
                        lines.append(f"  {r}")
                return "\n".join(lines)

            # Dict response
            lines = ["Normalization Results:"]
            for key, value in results.items():
                lines.append(f"  {key} → {value}")
            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] test_normalization failed: %s", e)
            return f"Error testing normalization: {e}"

    @mcp.tool()
    async def set_normalization_group_enabled(group_id: int, enabled: bool) -> str:
        """Enable or disable a normalization rule group globally.

        A normalization group must be globally enabled for its rules to be applied
        by the engine — even if the group is listed in an auto-creation rule's
        normalization_group_ids, the engine skips it when enabled=False.

        Use list_normalization_rules to discover group IDs and their current
        enabled status.

        Args:
            group_id: ID of the NormalizationRuleGroup to update.
            enabled: True to enable the group; False to disable it.
        """
        try:
            client = get_ecm_client()
            result = await client.call_endpoint(
                ENDPOINTS["normalization_update_group"],
                path_args={"group_id": group_id},
                body={"enabled": enabled},
            )
            name = result.get("name", f"group {group_id}") if isinstance(result, dict) else f"group {group_id}"
            state = "enabled" if enabled else "disabled"
            return f"Normalization group '{name}' (id={group_id}) is now {state}."
        except Exception as e:
            logger.error("[MCP] set_normalization_group_enabled failed: %s", e)
            return f"Error updating normalization group {group_id}: {e}"

    # -------------------------------------------------------------------
    # enhancedchannelmanager-e5n8j: normalization CRUD tools.
    # Group/rule condition/action vocabulary (backend/normalization_engine.py):
    #   condition_type: always, contains, starts_with, ends_with, regex, tag_group
    #   action_type / else_action_type: remove, replace, regex_replace,
    #     strip_prefix, strip_suffix, normalize_prefix, capitalize
    # -------------------------------------------------------------------

    @mcp.tool()
    async def create_normalization_group(
        name: str,
        description: str | None = None,
        enabled: bool = True,
        priority: int = 0,
    ) -> str:
        """Create a new normalization rule group.

        A group is a priority-ordered container for normalization rules — the
        group itself must be enabled (see set_normalization_group_enabled) for
        its rules to run, independent of each rule's own enabled flag.

        Args:
            name: Group name
            description: Optional description
            enabled: Whether the group is enabled (default True)
            priority: Execution priority — lower runs first (default 0)
        """
        try:
            client = get_ecm_client()
            body: dict = {"name": name, "enabled": enabled, "priority": priority}
            if description is not None:
                body["description"] = description
            result = await client.call_endpoint(ENDPOINTS["normalization_create_group"], body=body)
            gid = result.get("id", "?") if isinstance(result, dict) else "?"
            rname = result.get("name", name) if isinstance(result, dict) else name
            return f"Normalization group created: '{rname}' (id={gid})"
        except Exception as e:
            logger.error("[MCP] create_normalization_group failed: %s", e)
            return f"Error creating normalization group: {e}"

    @mcp.tool()
    async def update_normalization_group(
        group_id: int,
        name: str | None = None,
        description: str | None = None,
        enabled: bool | None = None,
        priority: int | None = None,
    ) -> str:
        """Update a normalization rule group. Only provided fields are changed.

        For just toggling enabled/disabled, set_normalization_group_enabled
        remains the narrower, purpose-built tool — this tool additionally
        covers renaming, re-describing, and re-prioritizing the group.

        Args:
            group_id: The group ID to update
            name: New group name
            description: New description
            enabled: Enable/disable the group
            priority: New execution priority (lower runs first)
        """
        try:
            client = get_ecm_client()
            payload = {}
            for field_name, value in [
                ("name", name), ("description", description),
                ("enabled", enabled), ("priority", priority),
            ]:
                if value is not None:
                    payload[field_name] = value

            if not payload:
                return "No changes specified."

            result = await client.call_endpoint(
                ENDPOINTS["normalization_update_group"], path_args={"group_id": group_id}, body=payload,
            )
            rname = result.get("name", "?") if isinstance(result, dict) else "?"
            return f"Normalization group {group_id} updated: name='{rname}'. Changed: {', '.join(payload.keys())}"
        except Exception as e:
            logger.error("[MCP] update_normalization_group failed: %s", e)
            return f"Error updating normalization group {group_id}: {e}"

    @mcp.tool()
    async def delete_normalization_group(group_id: int, confirm: bool = False) -> str:
        """Delete a normalization rule group AND ALL of its rules (cascade).

        CONFIRM GATING (bd-onazy convention): this is a two-call operation.
        The first call (``confirm=False``, the default) fetches the group and
        returns a preview naming it and the number of rules that would be
        cascade-deleted, deleting NOTHING. Re-invoke with ``confirm=True`` to
        actually delete.

        The backend also cleans the deleted group's id out of every
        channel-pipeline rule's normalization_group_ids so no rule is left
        holding a dangling reference (GH #465 / bd-miut3) — the delete result
        reports how many rules were cleaned.

        Args:
            group_id: The group ID to delete
            confirm: Set True on the second call to perform the deletion.
        """
        try:
            client = get_ecm_client()
            if not confirm:
                group = await client.call_endpoint(
                    ENDPOINTS["normalization_get_group"], path_args={"group_id": group_id}
                )
                name = group.get("name", "?") if isinstance(group, dict) else "?"
                rules = group.get("rules", []) if isinstance(group, dict) else []
                return (
                    f"Normalization group {group_id} '{name}' AND its {len(rules)} rule(s) "
                    f"will be deleted — re-invoke with confirm=True to delete."
                )
            result = await client.call_endpoint(
                ENDPOINTS["normalization_delete_group"], path_args={"group_id": group_id}
            )
            cleaned = result.get("rules_cleaned", 0) if isinstance(result, dict) else 0
            note = f" ({cleaned} channel-pipeline rule reference(s) cleaned)" if cleaned else ""
            return f"Normalization group {group_id} deleted{note}."
        except Exception as e:
            logger.error("[MCP] delete_normalization_group failed: %s", e)
            return f"Error deleting normalization group {group_id}: {e}"

    @mcp.tool()
    async def create_normalization_rule(
        group_id: int,
        name: str,
        action_type: str,
        description: str | None = None,
        enabled: bool = True,
        priority: int = 0,
        condition_type: str | None = None,
        condition_value: str | None = None,
        case_sensitive: bool = False,
        tag_group_id: int | None = None,
        tag_match_position: str | None = None,
        require_delimiter: bool = False,
        conditions: list[dict] | None = None,
        condition_logic: str = "AND",
        action_value: str | None = None,
        else_action_type: str | None = None,
        else_action_value: str | None = None,
        stop_processing: bool = False,
    ) -> str:
        """Create a new normalization rule within a group.

        A rule is: IF <condition> THEN <action> [ELSE <else_action>]. Use
        EITHER the legacy single condition (condition_type/condition_value) OR
        the compound conditions list — compound conditions take precedence
        when both are given.

        Args:
            group_id: The normalization group this rule belongs to
            name: Rule name
            action_type: Action to apply on match — one of 'remove', 'replace',
                'regex_replace', 'strip_prefix', 'strip_suffix',
                'normalize_prefix', 'capitalize'
            description: Optional description
            enabled: Whether the rule is enabled (default True)
            priority: Execution priority within the group — lower runs first
            condition_type: Legacy single condition — one of 'always',
                'contains', 'starts_with', 'ends_with', 'regex', 'tag_group'
            condition_value: Value/pattern for condition_type (not needed for
                'always' or 'tag_group')
            case_sensitive: Whether condition_value matching is case-sensitive
            tag_group_id: Tag group ID for condition_type='tag_group' (see
                list_tag_groups / create_tag_group)
            tag_match_position: 'prefix', 'suffix', or 'contains' — where the
                matched tag must sit (condition_type='tag_group')
            require_delimiter: Require a strong delimiter (':','-','|','/')
                adjacent to a matched tag rather than a bare space, e.g. only
                strip "NFL: X" and not "NFL X" (condition_type='tag_group')
            conditions: Compound conditions list, takes precedence over
                condition_type/condition_value: [{"type": <condition_type>,
                "value": ..., "negate": bool, "case_sensitive": bool}, ...]
            condition_logic: 'AND' or 'OR' — how compound conditions combine
                (default 'AND')
            action_value: Value for action_type (e.g. replacement text for
                'replace'/'regex_replace', prefix/suffix string for
                'strip_prefix'/'strip_suffix'/'normalize_prefix')
            else_action_type: Action to apply when the condition does NOT
                match — same vocabulary as action_type
            else_action_value: Value for else_action_type
            stop_processing: If True, stop evaluating further normalization
                rules once this rule fires (default False)
        """
        try:
            client = get_ecm_client()
            body: dict = {
                "group_id": group_id,
                "name": name,
                "enabled": enabled,
                "priority": priority,
                "case_sensitive": case_sensitive,
                "require_delimiter": require_delimiter,
                "condition_logic": condition_logic,
                "action_type": action_type,
                "stop_processing": stop_processing,
            }
            if description is not None:
                body["description"] = description
            if condition_type is not None:
                body["condition_type"] = condition_type
            if condition_value is not None:
                body["condition_value"] = condition_value
            if tag_group_id is not None:
                body["tag_group_id"] = tag_group_id
            if tag_match_position is not None:
                body["tag_match_position"] = tag_match_position
            if conditions is not None:
                body["conditions"] = conditions
            if action_value is not None:
                body["action_value"] = action_value
            if else_action_type is not None:
                body["else_action_type"] = else_action_type
            if else_action_value is not None:
                body["else_action_value"] = else_action_value

            result = await client.call_endpoint(ENDPOINTS["normalization_create_rule"], body=body)
            rid = result.get("id", "?") if isinstance(result, dict) else "?"
            rname = result.get("name", name) if isinstance(result, dict) else name
            return f"Normalization rule created: '{rname}' (id={rid}, group_id={group_id})"
        except Exception as e:
            logger.error("[MCP] create_normalization_rule failed: %s", e)
            return f"Error creating normalization rule: {e}"

    @mcp.tool()
    async def update_normalization_rule(
        rule_id: int,
        name: str | None = None,
        description: str | None = None,
        enabled: bool | None = None,
        priority: int | None = None,
        condition_type: str | None = None,
        condition_value: str | None = None,
        case_sensitive: bool | None = None,
        tag_group_id: int | None = None,
        tag_match_position: str | None = None,
        require_delimiter: bool | None = None,
        conditions: list[dict] | None = None,
        condition_logic: str | None = None,
        action_type: str | None = None,
        action_value: str | None = None,
        else_action_type: str | None = None,
        else_action_value: str | None = None,
        stop_processing: bool | None = None,
    ) -> str:
        """Update an existing normalization rule. Only provided fields are changed.

        See create_normalization_rule for the condition/action vocabulary. A
        rule's group_id is fixed at creation and cannot be moved via update.

        Args:
            rule_id: The rule ID to update
            name: New rule name
            description: New description
            enabled: Enable/disable the rule
            priority: New execution priority within the group
            condition_type: New legacy single condition type
            condition_value: New condition value/pattern
            case_sensitive: New case-sensitivity for condition_value matching
            tag_group_id: New tag group ID (condition_type='tag_group')
            tag_match_position: New tag match position ('prefix'/'suffix'/'contains')
            require_delimiter: New require-delimiter setting (tag_group)
            conditions: Replacement compound conditions list (replaces the
                existing list wholesale, not merged)
            condition_logic: New compound-condition logic ('AND'/'OR')
            action_type: New action type
            action_value: New action value
            else_action_type: New else-branch action type
            else_action_value: New else-branch action value
            stop_processing: New stop-processing setting
        """
        try:
            client = get_ecm_client()
            payload = {}
            for field_name, value in [
                ("name", name), ("description", description),
                ("enabled", enabled), ("priority", priority),
                ("condition_type", condition_type), ("condition_value", condition_value),
                ("case_sensitive", case_sensitive), ("tag_group_id", tag_group_id),
                ("tag_match_position", tag_match_position),
                ("require_delimiter", require_delimiter),
                ("conditions", conditions), ("condition_logic", condition_logic),
                ("action_type", action_type), ("action_value", action_value),
                ("else_action_type", else_action_type), ("else_action_value", else_action_value),
                ("stop_processing", stop_processing),
            ]:
                if value is not None:
                    payload[field_name] = value

            if not payload:
                return "No changes specified."

            result = await client.call_endpoint(
                ENDPOINTS["normalization_update_rule"], path_args={"rule_id": rule_id}, body=payload,
            )
            rname = result.get("name", "?") if isinstance(result, dict) else "?"
            return f"Normalization rule {rule_id} updated: name='{rname}'. Changed: {', '.join(payload.keys())}"
        except Exception as e:
            logger.error("[MCP] update_normalization_rule failed: %s", e)
            return f"Error updating normalization rule {rule_id}: {e}"

    @mcp.tool()
    async def delete_normalization_rule(rule_id: int, confirm: bool = False) -> str:
        """Delete a normalization rule.

        CONFIRM GATING (bd-onazy convention): this is a two-call operation.
        The first call (``confirm=False``, the default) fetches the rule and
        returns a preview naming it, deleting NOTHING. Re-invoke with
        ``confirm=True`` to actually delete.

        Args:
            rule_id: The rule ID to delete
            confirm: Set True on the second call to perform the deletion.
        """
        try:
            client = get_ecm_client()
            if not confirm:
                rule = await client.call_endpoint(
                    ENDPOINTS["normalization_get_rule"], path_args={"rule_id": rule_id}
                )
                name = rule.get("name", "?") if isinstance(rule, dict) else "?"
                return (
                    f"Normalization rule {rule_id} '{name}' will be deleted — "
                    f"re-invoke with confirm=True to delete."
                )
            await client.call_endpoint(ENDPOINTS["normalization_delete_rule"], path_args={"rule_id": rule_id})
            return f"Normalization rule {rule_id} deleted."
        except Exception as e:
            logger.error("[MCP] delete_normalization_rule failed: %s", e)
            return f"Error deleting normalization rule {rule_id}: {e}"

    @mcp.tool()
    async def apply_normalization_to_channels(
        dry_run: bool = True,
        actions: list[dict] | None = None,
    ) -> str:
        """Apply enabled normalization rules to existing channel names.

        PREVIEW-FIRST (dry_run defaults True): with dry_run=True (default),
        returns the per-channel diff (current name -> proposed normalized
        name, and whether the proposed name COLLIDES with another existing
        channel) WITHOUT changing anything. Review the diff, then re-invoke
        with dry_run=False and an explicit `actions` list to execute.

        EXECUTE MODE (dry_run=False): only channels you explicitly list in
        `actions` are touched — any channel with a pending diff that you don't
        list is SKIPPED (the safe default). Each action is
        {"channel_id": <int>, "action": "rename"|"merge"|"skip",
        "merge_target_id": <int, required for "merge" unless the diff already
        identifies a collision target>}. 'rename' is refused server-side if the
        proposed name collides with an existing channel (choose 'merge' or
        'skip' instead). 'merge' absorbs the channel's streams into
        merge_target_id and deletes the source channel. Every rename/merge is
        journaled for audit/undo review.

        Rate-limited to 5/minute by the backend to prevent runaway bulk-apply
        loops.

        Args:
            dry_run: If True (default), preview only — nothing is changed.
                Set False to execute.
            actions: Execute-mode per-channel decisions (ignored when
                dry_run=True). Required to change anything in execute mode —
                an execute call with no actions changes nothing (every
                pending channel is skipped).
        """
        try:
            client = get_ecm_client()
            body = None
            if not dry_run and actions:
                body = {"actions": actions}
            result = await client.call_endpoint(
                ENDPOINTS["normalization_apply_to_channels"],
                query={"dry_run": dry_run},
                body=body,
            )
            if not isinstance(result, dict):
                return "Apply-to-channels returned an unexpected response."

            if result.get("dry_run"):
                diffs = result.get("diffs", []) or []
                if not diffs:
                    return "No channels would change — every channel name already matches its normalized form."
                lines = [f"Preview: {len(diffs)} channel(s) would change (dry_run — nothing changed):"]
                for d in diffs[:30]:
                    cid = d.get("channel_id", "?")
                    cur = d.get("current_name", "?")
                    prop = d.get("proposed_name", "?")
                    collision = " [COLLISION with existing channel — rename would fail, use merge]" if d.get("collision") else ""
                    lines.append(f"  id={cid}: '{cur}' -> '{prop}'{collision}")
                if len(diffs) > 30:
                    lines.append(f"  ... and {len(diffs) - 30} more")
                lines.append(
                    "Re-invoke with dry_run=False and an `actions` list "
                    "({channel_id, action: rename|merge|skip, merge_target_id}) to execute."
                )
                return "\n".join(lines)

            renamed = result.get("renamed", []) or []
            merged = result.get("merged", []) or []
            skipped = result.get("skipped", []) or []
            errors = result.get("errors", []) or []
            lines = [
                f"Apply-to-channels complete: {len(renamed)} renamed, "
                f"{len(merged)} merged, {len(skipped)} skipped, {len(errors)} errors."
            ]
            if renamed:
                lines.append("Renamed:")
                for r in renamed[:10]:
                    lines.append(f"  id={r.get('channel_id')}: '{r.get('old_name')}' -> '{r.get('new_name')}'")
            if merged:
                lines.append("Merged:")
                for m in merged[:10]:
                    lines.append(f"  id={m.get('channel_id')} -> target {m.get('target_id')} (+{m.get('streams_added', 0)} streams)")
            if errors:
                lines.append("Errors:")
                for e in errors[:10]:
                    lines.append(f"  id={e.get('channel_id')}: {e.get('error')}")
            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] apply_normalization_to_channels failed: %s", e)
            return f"Error applying normalization to channels: {e}"

    @mcp.tool()
    async def list_normalization_rules() -> str:
        """List all normalization rule groups and their rules."""
        try:
            client = get_ecm_client()
            # Use the /rules endpoint — it returns groups WITH nested rules.
            # The /groups endpoint returns group metadata only (no rules).
            result = await client.call_endpoint(ENDPOINTS["normalization_list_rules"])

            groups = result.get("groups", []) if isinstance(result, dict) else result

            if not groups:
                return "No normalization rules configured."

            lines = [f"Normalization Rules ({len(groups)} groups):"]
            for g in groups:
                name = g.get("name", "Unknown")
                gid = g.get("id", "?")
                enabled = "enabled" if g.get("enabled", True) else "disabled"
                rules = g.get("rules") or []
                lines.append(f"\n  {name} (id={gid}) — {enabled}, {len(rules)} rules")
                for r in rules[:5]:
                    rname = r.get("name", "?")
                    rtype = r.get("action_type", r.get("condition_type", "?"))
                    lines.append(f"    - {rname} ({rtype})")
                if len(rules) > 5:
                    lines.append(f"    ... and {len(rules) - 5} more")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] list_normalization_rules failed: %s", e)
            return f"Error listing normalization rules: {e}"
