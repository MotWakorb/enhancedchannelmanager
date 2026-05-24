"""Auto-creation pipeline tools."""
import asyncio
import logging

from mcp.server.fastmcp import FastMCP

from _endpoint_contracts import ENDPOINTS
from ecm_client import get_ecm_client

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Polling constants for run_auto_creation (bd-1wq7z.8).
# Extracted as module-level names so tests can patch them cheaply.
# ---------------------------------------------------------------------------
_POLL_INTERVAL_SECONDS: float = 5.0   # seconds between status checks
_POLL_MAX_ATTEMPTS: int = 120          # cap at 120 × 5 s = 10 minutes

# Terminal statuses that end the poll loop.
_TERMINAL_STATUSES = frozenset({"completed", "failed", "rolled_back"})


async def _poll_sleep(seconds: float) -> None:
    """Thin asyncio.sleep wrapper — patchable in tests."""
    await asyncio.sleep(seconds)


def _action_descriptor(a: dict) -> str:
    """Return a human-readable descriptor for an auto-creation action.

    Actions are flattened ``{type, ...params}`` dicts whose descriptor field
    varies by type (auto_creation_schema): create_channel / create_group /
    merge_streams carry ``name_template``; merge_streams also has ``target``;
    assign_* / set_channel_number carry ``value``; assign_epg has ``epg_id``;
    assign_profile has ``profile_id``. Reading only ``value``/``target`` left
    create_channel rendering as ``?`` (lq38l.13 #4).
    """
    for key in (
        "name_template",
        "value",
        "target",
        "epg_id",
        "profile_id",
        "channel_profile_ids",
        "name",
    ):
        if a.get(key) not in (None, ""):
            return str(a[key])
    return "?"


def _format_analyze_result(result: dict, source: str) -> str:
    """Render a /rules/analyze response as a markdown report.

    Output shape::

        # Auto-creation rule analysis (<source>)

        Summary: 0 errors, 3 warnings, 0 info.

        ## <Rule name> (id=<n>)
        | Code | Severity | Field | Message |
        |---|---|---|---|
        | REGEX_TRIVIALLY_MATCHES_ALL | warning | conditions[1].value | … |

        ## <Next rule>
        No findings.

    The "no findings" branch is friendly — empty rule lists and
    finding-free responses both surface as a clear all-clean message.
    """
    summary = result.get("summary") or {}
    rules = result.get("rules") or []
    total = sum(summary.values()) if summary else 0

    lines = [f"# Auto-creation rule analysis ({source})", ""]
    if total == 0:
        lines.append(
            f"No findings across {len(rules)} rule(s) — looks clean."
        )
        return "\n".join(lines)

    lines.append(
        f"Summary: {summary.get('error', 0)} errors, "
        f"{summary.get('warning', 0)} warnings, "
        f"{summary.get('info', 0)} info."
    )
    lines.append("")
    for r in rules:
        rid = r.get("rule_id")
        name = r.get("rule_name") or "<unnamed>"
        findings = r.get("findings") or []
        header = f"## {name}"
        if rid is not None:
            header += f" (id={rid})"
        lines.append(header)
        if not findings:
            lines.append("No findings.")
            lines.append("")
            continue
        lines.append("| Code | Severity | Field | Message |")
        lines.append("|---|---|---|---|")
        for f in findings:
            msg = (f.get("message") or "").replace("\n", " ").replace("|", "\\|")
            field = (f.get("field") or "").replace("|", "\\|")
            lines.append(
                f"| {f.get('code', '?')} | {f.get('severity', '?')} | "
                f"{field} | {msg} |"
            )
        lines.append("")
    return "\n".join(lines)


def register(mcp: FastMCP):
    @mcp.tool()
    async def list_auto_creation_rules() -> str:
        """List all auto-creation rules that automatically create channels from streams."""
        try:
            client = get_ecm_client()
            resp = await client.call_endpoint(ENDPOINTS["ac_list_rules"])
            # The backend wraps the list as {"rules": [...]}; unwrap defensively
            # (older code iterated the dict's keys -> str.get() AttributeError,
            # bd-pvw35 / GH #222). The `analyze` tool below already does this.
            rules = resp.get("rules", []) if isinstance(resp, dict) else (resp or [])

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

        The backend returns 202 immediately and runs the pipeline in the
        background. This tool polls until the run completes (or times out),
        then reports the real results.

        Args:
            dry_run: If true (default), preview what would be created without making changes.
                     Set to false to actually create the channels.
        """
        try:
            client = get_ecm_client()

            # Kick off the run. Backend returns 202 + execution_id immediately.
            kickoff = await client.call_endpoint(
                ENDPOINTS["ac_run"], body={"dry_run": dry_run}
            )
            execution_id = kickoff.get("execution_id")
            if execution_id is None:
                logger.error("[MCP] run_auto_creation: no execution_id in 202 response: %s", kickoff)
                return "Error running auto-creation: backend did not return an execution_id"

            logger.info("[MCP] run_auto_creation started execution_id=%s dry_run=%s", execution_id, dry_run)

            # Poll until the execution reaches a terminal status.
            result = None
            for attempt in range(_POLL_MAX_ATTEMPTS):
                await _poll_sleep(_POLL_INTERVAL_SECONDS)
                try:
                    result = await client.call_endpoint(
                        ENDPOINTS["ac_get_execution"],
                        path_args={"execution_id": execution_id},
                    )
                except Exception as poll_err:
                    logger.warning(
                        "[MCP] run_auto_creation poll attempt %d failed: %s",
                        attempt + 1, poll_err,
                    )
                    continue

                status = result.get("status", "")
                logger.debug(
                    "[MCP] run_auto_creation poll attempt=%d status=%s",
                    attempt + 1, status,
                )
                if status in _TERMINAL_STATUSES:
                    break
            else:
                # Timed out — return execution_id so the user can check manually.
                return (
                    f"Auto-creation run is still running after {_POLL_MAX_ATTEMPTS} polls "
                    f"(execution_id={execution_id}). "
                    "Check status with list_auto_creation_executions."
                )

            # result is now the final execution row.
            status = result.get("status", "unknown")
            if status == "failed":
                err = result.get("error_message") or "unknown error"
                return (
                    f"Auto-creation run failed (execution_id={execution_id}): {err}"
                )

            mode = "Dry run" if dry_run else "Execution"
            dur = result.get("duration_seconds")
            dur_str = f"{dur:.1f}s" if dur is not None else "N/A"

            lines = [f"Auto-creation {mode} complete (execution_id={execution_id}):"]
            lines.append(f"  Streams evaluated: {result.get('streams_evaluated', 0)}")
            lines.append(f"  Streams matched: {result.get('streams_matched', 0)}")
            lines.append(
                f"  Channels {'would be ' if dry_run else ''}created: "
                f"{result.get('channels_created', 0)}"
            )
            lines.append(f"  Channels updated: {result.get('channels_updated', 0)}")
            lines.append(f"  Groups created: {result.get('groups_created', 0)}")
            lines.append(f"  Streams skipped: {result.get('streams_skipped', 0)}")
            lines.append(f"  Duration: {dur_str}")

            # Show rule match breakdown (present on some execution shapes)
            rule_counts = result.get("rule_match_counts", {})
            if rule_counts:
                lines.append(f"  Rule matches: {rule_counts}")

            # Show a sample of the entities that were / would be created.
            #
            # lq38l.13 #5: the dry-run path used to read raw dry_run_results
            # rows — which have no channel_name/channel_number (only stream_name
            # + an `action` string + would_create), so every sample rendered "?"
            # and the "N more" counted ALL simulated actions (e.g. 482) while the
            # summary's "Channels would be created" counted only would_create
            # rows (e.g. 45). Filter dry_run_results to would_create entries so
            # the sample and the "N more" line are consistent with the count.
            if dry_run:
                all_rows = result.get("dry_run_results", []) or []
                created = [r for r in all_rows if r.get("would_create")]
                label = "would be created"
            else:
                created = result.get("created_entities", []) or []
                label = "created"

            if created:
                lines.append(f"\n  Sample channels ({label}):")
                for entity in created[:20]:
                    if dry_run:
                        # dry_run rows: source stream name + the action taken.
                        name = entity.get("stream_name") or "?"
                        action_desc = entity.get("action", "")
                        detail = f" — {action_desc}" if action_desc else ""
                        lines.append(f"    {name}{detail}")
                    else:
                        # created_entities rows: {type, id, name}.
                        name = entity.get("name", entity.get("channel_name", "?"))
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
            r = await client.call_endpoint(ENDPOINTS["ac_get_rule"], path_args={"rule_id": rule_id})

            lines = [
                f"Rule: {r.get('name', 'Unnamed')}",
                f"  ID: {r.get('id')}",
                f"  Enabled: {r.get('enabled', False)}",
                f"  Priority: {r.get('priority', '?')}",
            ]

            if r.get("description"):
                lines.append(f"  Description: {r['description']}")

            lines.append(f"  Run on refresh: {r.get('run_on_refresh', False)}")
            lines.append(f"  Stop on first match: {r.get('stop_on_first_match', True)}")
            lines.append(f"  Skip struck streams: {r.get('skip_struck_streams', False)}")
            lines.append(f"  Orphan action: {r.get('orphan_action', 'delete')}")

            # Sort settings
            if r.get("sort_field"):
                lines.append(f"  Channel sort: {r['sort_field']} {r.get('sort_order', 'asc')}")
            if r.get("stream_sort_field"):
                lines.append(f"  Stream sort: {r['stream_sort_field']} {r.get('stream_sort_order', 'asc')}")

            # Normalization groups
            norm_ids = r.get("normalization_group_ids", [])
            if norm_ids:
                lines.append(f"  Normalization groups: {norm_ids}")

            conditions = r.get("conditions", [])
            if conditions:
                lines.append(f"  Conditions ({len(conditions)}):")
                for c in conditions[:10]:
                    lines.append(f"    - {c.get('type', '?')}: {c.get('value', c.get('pattern', '?'))}")
                if len(conditions) > 10:
                    lines.append(f"    ... and {len(conditions) - 10} more")

            actions = r.get("actions", [])
            if actions:
                lines.append(f"  Actions ({len(actions)}):")
                for a in actions[:10]:
                    lines.append(f"    - {a.get('type', '?')}: {_action_descriptor(a)}")
                if len(actions) > 10:
                    lines.append(f"    ... and {len(actions) - 10} more")

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
            result = await client.call_endpoint(ENDPOINTS["ac_toggle_rule"], path_args={"rule_id": rule_id})
            enabled = result.get("enabled", "unknown")
            return f"Rule {rule_id} is now {'enabled' if enabled else 'disabled'}."
        except Exception as e:
            logger.error("[MCP] toggle_auto_creation_rule failed: %s", e)
            return f"Error toggling rule {rule_id}: {e}"

    @mcp.tool()
    async def bulk_toggle_auto_creation_rules(rule_ids: list[int]) -> str:
        """Toggle multiple auto-creation rules at once (enable/disable).

        Args:
            rule_ids: List of rule IDs to toggle
        """
        try:
            client = get_ecm_client()
            results = []
            errors = []
            for rid in rule_ids:
                try:
                    result = await client.call_endpoint(ENDPOINTS["ac_toggle_rule"], path_args={"rule_id": rid})
                    enabled = result.get("enabled", "unknown")
                    state = "enabled" if enabled else "disabled"
                    results.append(f"Rule {rid}: {state}")
                except Exception as e:
                    errors.append(f"Rule {rid}: {e}")

            lines = [f"Toggled {len(results)}/{len(rule_ids)} rules:"]
            for r in results:
                lines.append(f"  - {r}")
            if errors:
                lines.append(f"Errors ({len(errors)}):")
                for err in errors:
                    lines.append(f"  - {err}")
            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] bulk_toggle_auto_creation_rules failed: %s", e)
            return f"Error toggling rules: {e}"

    @mcp.tool()
    async def duplicate_auto_creation_rule(rule_id: int) -> str:
        """Duplicate an auto-creation rule.

        Args:
            rule_id: The rule ID to duplicate
        """
        try:
            client = get_ecm_client()
            result = await client.call_endpoint(ENDPOINTS["ac_duplicate_rule"], path_args={"rule_id": rule_id})
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
            await client.call_endpoint(ENDPOINTS["ac_delete_rule"], path_args={"rule_id": rule_id})
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
        normalization_group_ids: list[int] | None = None,
        skip_struck_streams: bool = False,
        orphan_action: str = "delete",
        quality_tie_break_order: str | None = None,
        match_scope_target_group: bool | None = None,
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
                Example: [{"type": "stream_group_contains", "value": "USA | Entertainment", "connector": "and"}]
            actions: List of action dicts. Each has 'type' and type-specific fields.
                Action types:
                  create_group — params: name_template, if_exists (skip/use_existing)
                  create_channel — params: name_template, if_exists (skip/merge/merge_only/update),
                                   channel_number (e.g. "800-99999" for range)
                  merge_streams — params: target (auto/existing_channel/new_channel),
                                   find_channel_by (name_exact/name_regex/tvg_id) + find_channel_value,
                                   max_streams_per_channel, remove_non_matching (bool),
                                   loose_name_match (bool, default false). With target=auto the
                                   stream merges into an existing channel only on EXACT normalized-
                                   name equality; set loose_name_match=true to restore the legacy
                                   fuzzy cascade (core-name/deparen/word-prefix/call-sign).
                                   NOTE: match_by is a DEPRECATED no-op (validated but never
                                   consumed at runtime) — use loose_name_match to control matching.
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
            stream_sort_field: How to sort streams within channels ('smart_sort', 'resolution', 'video_codec', etc.)
            stream_sort_order: 'asc' or 'desc'
            normalization_group_ids: List of normalization group IDs to apply (use list_normalization_rules to see available groups)
            skip_struck_streams: Skip streams with consecutive probe failures
            orphan_action: What to do with orphaned channels ('delete', 'keep', 'disable')
            quality_tie_break_order: Tie-break order ('asc' or 'desc') when two streams
                have equal quality during quality-based M3U sorting (backend default 'desc')
            match_scope_target_group: When True, restrict the existing-channel name
                lookup (for merge/skip decisions) to the rule's target group
                instead of searching all groups (backend default True)
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
            if normalization_group_ids is not None:
                payload["normalization_group_ids"] = normalization_group_ids
            # lq38l.13 #12: both fields are accepted by the backend
            # CreateAutoCreationRuleRequest and persisted by the create handler.
            if quality_tie_break_order is not None:
                payload["quality_tie_break_order"] = quality_tie_break_order
            if match_scope_target_group is not None:
                payload["match_scope_target_group"] = match_scope_target_group

            result = await client.call_endpoint(ENDPOINTS["ac_create_rule"], body=payload)

            rule = result.get("rule", result)
            new_id = rule.get("id", "?")
            return f"Created auto-creation rule '{name}' (id={new_id})."
        except Exception as e:
            logger.error("[MCP] create_auto_creation_rule failed: %s", e)
            return f"Error creating rule: {e}"

    @mcp.tool()
    async def update_auto_creation_rule(
        rule_id: int,
        name: str | None = None,
        description: str | None = None,
        enabled: bool | None = None,
        priority: int | None = None,
        m3u_account_id: int | None = None,
        target_group_id: int | None = None,
        conditions: list[dict] | None = None,
        actions: list[dict] | None = None,
        run_on_refresh: bool | None = None,
        stop_on_first_match: bool | None = None,
        sort_field: str | None = None,
        sort_order: str | None = None,
        probe_on_sort: bool | None = None,
        sort_regex: str | None = None,
        stream_sort_field: str | None = None,
        stream_sort_order: str | None = None,
        normalization_group_ids: list[int] | None = None,
        skip_struck_streams: bool | None = None,
        orphan_action: str | None = None,
    ) -> str:
        """Update an existing auto-creation rule. Only provided fields are changed.

        Args:
            rule_id: The rule ID to update
            name: New rule name
            description: New description
            enabled: Enable/disable the rule
            priority: Execution priority (lower = first)
            m3u_account_id: M3U account filter
            target_group_id: Target channel group ID
            conditions: Replacement conditions list (see create_auto_creation_rule for types)
            actions: Replacement actions list (see create_auto_creation_rule for types)
            run_on_refresh: Run automatically when M3U refreshes
            stop_on_first_match: Stop matching after first rule matches a stream
            sort_field: Field to sort channels by
            sort_order: 'asc' or 'desc'
            probe_on_sort: Probe streams when sorting
            sort_regex: Regex for extracting sort keys
            stream_sort_field: How to sort streams within channels ('smart_sort', 'resolution', 'video_codec', etc.)
            stream_sort_order: 'asc' or 'desc'
            normalization_group_ids: List of normalization group IDs to apply (use list_normalization_rules to see available groups)
            skip_struck_streams: Skip streams with consecutive probe failures
            orphan_action: What to do with orphaned channels ('delete', 'keep', 'disable')
        """
        try:
            client = get_ecm_client()
            payload = {}
            # Only include fields that were explicitly provided
            for field_name, value in [
                ("name", name), ("description", description), ("enabled", enabled),
                ("priority", priority), ("m3u_account_id", m3u_account_id),
                ("target_group_id", target_group_id), ("conditions", conditions),
                ("actions", actions), ("run_on_refresh", run_on_refresh),
                ("stop_on_first_match", stop_on_first_match), ("sort_field", sort_field),
                ("sort_order", sort_order), ("probe_on_sort", probe_on_sort),
                ("sort_regex", sort_regex), ("stream_sort_field", stream_sort_field),
                ("stream_sort_order", stream_sort_order), ("normalization_group_ids", normalization_group_ids),
                ("skip_struck_streams", skip_struck_streams), ("orphan_action", orphan_action),
            ]:
                if value is not None:
                    payload[field_name] = value

            if not payload:
                return "No fields to update."

            result = await client.call_endpoint(
                ENDPOINTS["ac_update_rule"], path_args={"rule_id": rule_id}, body=payload,
            )
            rule = result.get("rule", result)
            return f"Updated rule '{rule.get('name', rule_id)}' (id={rule_id}). Changed: {', '.join(payload.keys())}"
        except Exception as e:
            logger.error("[MCP] update_auto_creation_rule failed: %s", e)
            return f"Error updating rule {rule_id}: {e}"

    @mcp.tool()
    async def list_auto_creation_executions(limit: int = 10) -> str:
        """List recent auto-creation pipeline executions.

        Args:
            limit: Number of executions to return (default 10)
        """
        try:
            client = get_ecm_client()
            result = await client.call_endpoint(ENDPOINTS["ac_list_executions"], query={"limit": limit})

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
            result = await client.call_endpoint(
                ENDPOINTS["ac_rollback"], path_args={"execution_id": execution_id}, timeout=300.0,
            )
            # Engine returns entities_removed (channels deleted) and
            # entities_restored (streams/entities un-merged). Neither
            # "deleted" nor "channels_deleted" exists in the response shape
            # — reading those keys always produced 0 (bd-1wq7z.5).
            removed = result.get("entities_removed", 0)
            restored = result.get("entities_restored", 0)
            msg = f"Execution {execution_id} rolled back. {removed} channel(s) deleted"
            if restored:
                msg += f", {restored} entit{'y' if restored == 1 else 'ies'} restored"
            msg += "."
            return msg
        except Exception as e:
            logger.error("[MCP] rollback_auto_creation failed: %s", e)
            return f"Error rolling back execution {execution_id}: {e}"

    @mcp.tool()
    async def analyze_auto_creation_rules(bundle_path: str | None = None) -> str:
        """Lint and structurally analyze auto-creation rules (bd-0gntx).

        Returns a markdown report of advisory findings: regex shapes that
        match everything by accident, operator/value mismatches, OR-arms
        that drop a guard, and merge_streams targeting empty channel
        groups. Findings are warnings — they never block rule saves.

        Args:
            bundle_path: Optional. If set, analyze rules.yaml from a
                debug-bundle tar.gz at that filesystem path (the file
                must exist on the MCP host). If unset, analyze the live
                rules in the connected ECM instance.
        """
        import os

        try:
            client = get_ecm_client()
            if bundle_path:
                if not os.path.isfile(bundle_path):
                    return f"Bundle file not found: {bundle_path}"
                with open(bundle_path, "rb") as fh:
                    content = fh.read()
                filename = os.path.basename(bundle_path) or "debug.tar.gz"
                # contract-exempt: multipart/form-data upload, not a JSON body —
                # call_endpoint only models JSON-body endpoints.
                result = await client.post_multipart(
                    "/api/auto-creation/rules/analyze/from-bundle",
                    files={"file": (filename, content, "application/gzip")},
                )
                source = f"bundle {filename}"
            else:
                result = await client.call_endpoint(ENDPOINTS["ac_analyze_rules"])
                source = "live ECM"

            return _format_analyze_result(result, source)
        except Exception as e:
            logger.error("[MCP] analyze_auto_creation_rules failed: %s", e)
            return f"Error analyzing auto-creation rules: {e}"

    @mcp.tool()
    async def get_auto_creation_debug_bundle() -> str:
        """Get info about the auto-creation debug bundle for troubleshooting.

        The debug bundle is a tar.gz file that must be downloaded from the ECM UI.
        This tool describes what it contains and how to get it.
        """
        return ("Debug bundle endpoints (bd-cns7j 202+poll):\n"
                "  POST /api/auto-creation/debug-bundle           -> 202 + {job_id}\n"
                "  GET  /api/auto-creation/debug-bundle/{job_id}  -> JSON status, or tar.gz when ready\n"
                "Or download it from the ECM UI: Auto-Creation page > Debug Bundle button.\n\n"
                "Bundle contains (all data obfuscated for safe sharing):\n"
                "  - channels.json — channel data with stream details and stats\n"
                "  - rules.yaml — auto-creation rules configuration\n"
                "  - normalization_rules.yaml — normalization rule groups + rules (cross-references rules.yaml's normalization_group_ids)\n"
                "  - channels.csv — all streams with metadata\n"
                "  - settings.json — app settings (credentials redacted)\n"
                "  - task_schedules.json — scheduled task configuration\n"
                "  - channel_groups_diagnostic.json — Channel Manager group/membership diagnostic\n"
                "  - logs.txt — recent application logs\n"
                "  - manifest.json — bundle metadata + counts")
