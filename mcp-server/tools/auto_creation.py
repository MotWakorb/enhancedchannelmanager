"""Auto-creation pipeline tools."""
import logging

from mcp.server.fastmcp import FastMCP

from ecm_client import get_ecm_client

logger = logging.getLogger(__name__)


def register(mcp: FastMCP):
    @mcp.tool()
    async def list_auto_creation_rules() -> str:
        """List all auto-creation rules that automatically create channels from streams."""
        try:
            client = get_ecm_client()
            rules = await client.get("/api/auto-creation/rules")

            if not rules:
                return "No auto-creation rules configured."

            lines = [f"Found {len(rules)} auto-creation rules:"]
            for r in rules:
                name = r.get("name", "Unnamed")
                rid = r.get("id", "?")
                enabled = "enabled" if r.get("enabled") else "disabled"
                priority = r.get("priority", "?")
                lines.append(f"  [{priority}] {name} (id={rid}) — {enabled}")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] list_auto_creation_rules failed: %s", e)
            return f"Error listing auto-creation rules: {e}"

    @mcp.tool()
    async def run_auto_creation(dry_run: bool = True) -> str:
        """Run the auto-creation pipeline to create channels from matching streams.

        Args:
            dry_run: If true (default), preview what would be created without making changes.
                     Set to false to actually create the channels.
        """
        try:
            client = get_ecm_client()
            result = await client.post("/api/auto-creation/run", json_data={"dry_run": dry_run}, timeout=300.0)

            mode = "Dry run" if dry_run else "Execution"
            lines = [f"Auto-creation {mode} complete:"]
            lines.append(f"  Streams evaluated: {result.get('streams_evaluated', 0)}")
            lines.append(f"  Streams matched: {result.get('streams_matched', 0)}")
            lines.append(f"  Channels {'would be ' if dry_run else ''}created: {result.get('channels_created', 0)}")
            lines.append(f"  Channels updated: {result.get('channels_updated', 0)}")
            lines.append(f"  Groups created: {result.get('groups_created', 0)}")
            lines.append(f"  Streams skipped: {result.get('streams_skipped', 0)}")
            lines.append(f"  Duration: {result.get('duration_seconds', 0):.1f}s")

            # Show rule match breakdown
            rule_counts = result.get("rule_match_counts", {})
            if rule_counts:
                lines.append(f"  Rule matches: {rule_counts}")

            # Show sample of created entities
            created = result.get("created_entities", [])
            if created:
                lines.append(f"\n  Sample channels ({'would be ' if dry_run else ''}created):")
                for entity in created[:20]:
                    name = entity.get("channel_name", entity.get("name", "?"))
                    num = entity.get("channel_number", "")
                    num_str = f" #{num}" if num else ""
                    lines.append(f"    {name}{num_str}")
                if len(created) > 20:
                    lines.append(f"    ... and {len(created) - 20} more")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] run_auto_creation failed: %s", e)
            return f"Error running auto-creation: {e}"

    @mcp.tool()
    async def get_auto_creation_rule(rule_id: int) -> str:
        """Get detailed information about a specific auto-creation rule.

        Args:
            rule_id: The rule ID to look up
        """
        try:
            client = get_ecm_client()
            r = await client.get(f"/api/auto-creation/rules/{rule_id}")

            lines = [
                f"Rule: {r.get('name', 'Unnamed')}",
                f"  ID: {r.get('id')}",
                f"  Enabled: {r.get('enabled', False)}",
                f"  Priority: {r.get('priority', '?')}",
            ]

            conditions = r.get("conditions", [])
            if conditions:
                lines.append(f"  Conditions ({len(conditions)}):")
                for c in conditions[:5]:
                    lines.append(f"    - {c.get('type', '?')}: {c.get('value', c.get('pattern', '?'))}")

            actions = r.get("actions", [])
            if actions:
                lines.append(f"  Actions ({len(actions)}):")
                for a in actions[:5]:
                    lines.append(f"    - {a.get('type', '?')}: {a.get('value', a.get('target', '?'))}")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_auto_creation_rule failed: %s", e)
            return f"Error getting rule {rule_id}: {e}"

    @mcp.tool()
    async def toggle_auto_creation_rule(rule_id: int) -> str:
        """Enable or disable an auto-creation rule (toggles current state).

        Args:
            rule_id: The rule ID to toggle
        """
        try:
            client = get_ecm_client()
            result = await client.post(f"/api/auto-creation/rules/{rule_id}/toggle")
            enabled = result.get("enabled", "unknown")
            return f"Rule {rule_id} is now {'enabled' if enabled else 'disabled'}."
        except Exception as e:
            logger.error("[MCP] toggle_auto_creation_rule failed: %s", e)
            return f"Error toggling rule {rule_id}: {e}"

    @mcp.tool()
    async def duplicate_auto_creation_rule(rule_id: int) -> str:
        """Duplicate an auto-creation rule.

        Args:
            rule_id: The rule ID to duplicate
        """
        try:
            client = get_ecm_client()
            result = await client.post(f"/api/auto-creation/rules/{rule_id}/duplicate")
            new_id = result.get("id", "?")
            return f"Rule {rule_id} duplicated. New rule ID: {new_id}"
        except Exception as e:
            logger.error("[MCP] duplicate_auto_creation_rule failed: %s", e)
            return f"Error duplicating rule {rule_id}: {e}"

    @mcp.tool()
    async def delete_auto_creation_rule(rule_id: int) -> str:
        """Delete an auto-creation rule.

        Args:
            rule_id: The rule ID to delete
        """
        try:
            client = get_ecm_client()
            await client.delete(f"/api/auto-creation/rules/{rule_id}")
            return f"Rule {rule_id} deleted."
        except Exception as e:
            logger.error("[MCP] delete_auto_creation_rule failed: %s", e)
            return f"Error deleting rule {rule_id}: {e}"

    @mcp.tool()
    async def create_auto_creation_rule(
        name: str,
        conditions: list[dict],
        actions: list[dict],
        description: str | None = None,
        enabled: bool = True,
        priority: int = 0,
        m3u_account_id: int | None = None,
        target_group_id: int | None = None,
        run_on_refresh: bool = False,
        stop_on_first_match: bool = True,
        sort_field: str | None = None,
        sort_order: str = "asc",
        probe_on_sort: bool = False,
        sort_regex: str | None = None,
        stream_sort_field: str | None = None,
        stream_sort_order: str = "asc",
        normalize_names: bool = False,
        skip_struck_streams: bool = False,
        orphan_action: str = "delete",
    ) -> str:
        """Create a new auto-creation rule.

        Args:
            name: Rule name
            conditions: List of condition dicts. Each has 'type', 'value', and optional 'connector' ("and"/"or").
                Condition types:
                  stream_name_contains — substring match on stream name
                  stream_name_matches — regex match on stream name
                  stream_group_contains — substring match on group name
                  stream_group_matches — regex match on group name
                  provider_is — from specific M3U account (value = account ID)
                  tvg_id_exists — stream has EPG ID (no value needed)
                  tvg_id_matches — regex match on EPG ID
                  logo_exists — stream has logo URL
                  quality_min / quality_max — min/max resolution height
                  codec_is — video codec filter
                  has_audio_tracks — minimum audio tracks
                  has_channel — stream already assigned to a channel
                  channel_exists_with_name — exact channel name exists
                  channel_exists_matching — regex match on existing channels
                  normalized_name_in_group / normalized_name_not_in_group
                  normalized_name_exists / normalized_name_not_exists
                  always / never — always or never matches
                Example: [{"type": "stream_group_contains", "value": "USA | Entertainment 📺", "connector": "and"}]
            actions: List of action dicts. Each has 'type' and type-specific fields.
                Action types:
                  create_group — params: name_template, if_exists (skip/use_existing)
                  create_channel — params: name_template, if_exists (skip/merge/merge_only/update),
                                   channel_number (e.g. "800-99999" for range)
                  merge_streams — params: name_template, match_by (tvg_id/normalized_name/stream_group)
                  assign_logo — params: value (URL or empty for stream logo)
                  assign_tvg_id — params: value
                  assign_epg — params: epg_id, set_tvg_id (bool)
                  assign_profile — params: profile_id
                  assign_channel_profile — params: channel_profile_ids (list)
                  set_channel_number — params: value
                  set_variable — params: name, value
                  remove_from_channel — remove stream from current channel
                  set_stream_priority — params: value
                  probe_streams — trigger probe
                  skip — skip this stream
                  stop_processing — stop processing further rules
                  log_match — log when matched
                Example: [{"type": "create_group", "name_template": "Entertainment", "if_exists": "use_existing"},
                          {"type": "create_channel", "name_template": "{stream_name}", "if_exists": "merge"}]
            description: Optional description
            enabled: Whether the rule is enabled (default true)
            priority: Execution priority (lower = first, default 0)
            m3u_account_id: Optional M3U account filter
            target_group_id: Optional target channel group ID
            run_on_refresh: Run automatically when M3U refreshes
            stop_on_first_match: Stop matching after first rule matches a stream
            sort_field: Field to sort channels by (e.g. 'stream_name', 'stream_name_regex')
            sort_order: 'asc' or 'desc'
            probe_on_sort: Probe streams when sorting
            sort_regex: Regex for extracting sort keys
            stream_sort_field: How to sort streams within channels ('smart_sort', 'resolution', etc.)
            stream_sort_order: 'asc' or 'desc'
            normalize_names: Apply normalization rules to stream names
            skip_struck_streams: Skip streams with consecutive probe failures
            orphan_action: What to do with orphaned channels ('delete', 'keep', 'disable')
        """
        try:
            client = get_ecm_client()
            payload = {
                "name": name,
                "conditions": conditions,
                "actions": actions,
                "enabled": enabled,
                "priority": priority,
                "run_on_refresh": run_on_refresh,
                "stop_on_first_match": stop_on_first_match,
                "sort_order": sort_order,
                "probe_on_sort": probe_on_sort,
                "stream_sort_order": stream_sort_order,
                "normalize_names": normalize_names,
                "skip_struck_streams": skip_struck_streams,
                "orphan_action": orphan_action,
            }
            if description is not None:
                payload["description"] = description
            if m3u_account_id is not None:
                payload["m3u_account_id"] = m3u_account_id
            if target_group_id is not None:
                payload["target_group_id"] = target_group_id
            if sort_field is not None:
                payload["sort_field"] = sort_field
            if sort_regex is not None:
                payload["sort_regex"] = sort_regex
            if stream_sort_field is not None:
                payload["stream_sort_field"] = stream_sort_field

            result = await client.post("/api/auto-creation/rules", json_data=payload)

            rule = result.get("rule", result)
            new_id = rule.get("id", "?")
            return f"Created auto-creation rule '{name}' (id={new_id})."
        except Exception as e:
            logger.error("[MCP] create_auto_creation_rule failed: %s", e)
            return f"Error creating rule: {e}"

    @mcp.tool()
    async def list_auto_creation_executions(limit: int = 10) -> str:
        """List recent auto-creation pipeline executions.

        Args:
            limit: Number of executions to return (default 10)
        """
        try:
            client = get_ecm_client()
            result = await client.get("/api/auto-creation/executions", limit=limit)

            executions = result.get("executions", []) if isinstance(result, dict) else result

            if not executions:
                return "No auto-creation executions found."

            lines = [f"Recent executions ({len(executions)}):"]
            for ex in executions[:limit]:
                eid = ex.get("id", "?")
                status = ex.get("status", "?")
                created = ex.get("created_at", ex.get("timestamp", "?"))
                channels = ex.get("channels_created", ex.get("created", 0))
                dry = " (dry run)" if ex.get("dry_run") else ""
                lines.append(f"  #{eid}: {status} — {channels} channels{dry} ({created})")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] list_auto_creation_executions failed: %s", e)
            return f"Error listing executions: {e}"

    @mcp.tool()
    async def rollback_auto_creation(execution_id: int) -> str:
        """Rollback an auto-creation execution, deleting all channels it created.

        Args:
            execution_id: The execution ID to rollback
        """
        try:
            client = get_ecm_client()
            result = await client.post(f"/api/auto-creation/executions/{execution_id}/rollback", timeout=300.0)
            deleted = result.get("deleted", result.get("channels_deleted", 0))
            return f"Execution {execution_id} rolled back. {deleted} channels deleted."
        except Exception as e:
            logger.error("[MCP] rollback_auto_creation failed: %s", e)
            return f"Error rolling back execution {execution_id}: {e}"
