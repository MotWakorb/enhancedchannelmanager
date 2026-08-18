"""Fail-closed safety inventory and two-call guard for ECM MCP tools.

The inventory is deliberately exhaustive.  Registration fails when the live
FastMCP registry differs, making classification a required part of adding or
renaming a tool.  Destructive and bulk-mutating tools are wrapped at the
registry boundary so their first call can never enter tool implementation code.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from enum import Enum
from types import MethodType
from typing import Any

from mcp.types import ToolAnnotations


class ToolSafety(str, Enum):
    READ_ONLY = "read-only"
    MUTATING = "mutating"
    DESTRUCTIVE = "destructive"


# Inventory generated from the actual FastMCP registry on 2026-08-17 and kept
# explicit so registry drift fails closed. Aliases are inventory entries too.
_ALL_TOOLS = frozenset("""
accept_channel_merge add_sd_lineup add_stream add_stream_to_channel add_tags_to_group
analyze_auto_creation_rules analyze_channel_pipeline_rules apply_normalization_to_channels
apply_profile_to_channels assign_channel_numbers audit_epg_duplicates build_channel_lineup
bulk_add_streams_to_channel bulk_assign_epg bulk_commit_channels bulk_delete_channels
bulk_merge_duplicate_channels bulk_remove_streams bulk_search_streams
bulk_toggle_auto_creation_rules bulk_toggle_channel_pipeline_rules bulk_update_m3u_group_settings
cancel_probe cancel_task cleanup_struck_out_streams clear_auto_created clear_emby_logos
compute_stream_sort create_auto_creation_rule create_backup create_channel create_channel_group
create_channel_pipeline_rule create_cloud_target create_dbas_backup create_dummy_epg_profile
create_epg_source create_event_sync_exclusion create_logo create_m3u_account
create_normalization_group create_normalization_rule create_sync_target create_tag_group
create_task_schedule delete_all_notifications delete_auto_creation_rule delete_channel
delete_channel_group delete_channel_pipeline_rule delete_cloud_target delete_dummy_epg_profile
delete_epg_source delete_event_sync_exclusion delete_logo delete_m3u_account
delete_normalization_group delete_normalization_rule delete_orphaned_groups delete_saved_backup
delete_sync_target delete_tag delete_tag_group delete_task_schedule dismiss_channel_merge
dismiss_probe_failures duplicate_auto_creation_rule duplicate_channel_pipeline_rule
export_channels_csv find_duplicate_channels fuzzy_match_stream generate_dummy_epg get_activity
get_auto_created_groups get_auto_creation_debug_bundle get_auto_creation_rule get_bandwidth
get_channel get_channel_bandwidth get_channel_pipeline_circuit_breaker
get_channel_pipeline_debug_bundle get_channel_pipeline_rule get_channel_popularity
get_channel_stats get_dummy_epg_profile get_epg_grid get_event_sync_team_aliases
get_export_sections get_groups_with_streams get_hidden_groups get_journal get_m3u_account
get_m3u_digest_settings get_orphaned_groups get_popularity_rankings get_probe_history
get_probe_progress get_probe_results get_provider_stats get_settings get_stale_stream_ids
get_stale_streams get_stream_health get_streams_by_ids get_streams_for_channel
get_struck_out_streams get_task_history get_top_watched get_trending get_unique_viewers
get_user_channel_breakdown get_user_watch_time get_watch_history import_channels_csv
link_channel_epg list_alert_methods list_auto_creation_executions list_auto_creation_rules
list_channel_groups list_channel_pipeline_executions list_channel_pipeline_rules
list_channel_profiles list_channels list_cloud_targets list_dismissed_probe_failures
list_dummy_epg_profiles list_epg_sources list_event_sync_exclusions list_logos
list_m3u_accounts list_normalization_rules list_notifications list_pending_channel_merges
list_saved_backups list_sd_lineups list_stream_profiles list_streams list_sync_targets
list_tag_groups list_task_schedules list_tasks mark_notifications_read match_channels_epg
match_streams_to_channels merge_channels preview_channels_csv preview_dummy_epg
preview_event_sync preview_fuzzy_matches probe_bulk_streams probe_single_stream probe_streams
refresh_all_epg refresh_all_m3u refresh_epg refresh_m3u remove_sd_lineup
remove_stream_from_channel reorder_streams reset_channel_pipeline_circuit_breaker
reset_probe_state restore_auto_creation_snapshot restore_backup restore_channel_pipeline_snapshot
restore_dbas_backup_saved rollback_auto_creation rollback_channel_pipeline run_auto_creation
run_channel_pipeline run_task search_sd_lineups search_streams set_logo_from_epg
set_m3u_account_priority set_normalization_group_enabled test_alert_method test_cloud_target
test_normalization test_tag_group toggle_auto_creation_rule toggle_channel_pipeline_rule
update_auto_creation_rule update_channel update_channel_pipeline_rule update_cloud_target
update_dummy_epg_profile update_epg_source update_event_sync_team_aliases update_logo
update_m3u_account update_m3u_digest_settings update_m3u_group_settings
update_normalization_group update_normalization_rule update_sync_target update_tag update_tag_group
""".split())

_READ_ONLY_TOOLS = frozenset(name for name in _ALL_TOOLS if name.startswith((
    "analyze_", "audit_", "compute_", "export_", "find_", "get_", "list_",
    "preview_", "search_", "test_",
))) | {"bulk_search_streams"}

# These remove/overwrite state, perform a bulk mutation, or launch a fan-out
# whose resolved selection can affect many records. They all use the uniform
# expiring confirmation flow below.
_DESTRUCTIVE_TOOLS = frozenset(name for name in _ALL_TOOLS if name.startswith((
    "delete_", "clear_", "cleanup_", "remove_", "reset_", "restore_", "rollback_",
))) | frozenset({
    "apply_normalization_to_channels", "apply_profile_to_channels", "assign_channel_numbers",
    "build_channel_lineup", "bulk_add_streams_to_channel", "bulk_assign_epg",
    "bulk_commit_channels", "bulk_delete_channels", "bulk_merge_duplicate_channels",
    "bulk_remove_streams", "bulk_toggle_auto_creation_rules",
    "bulk_toggle_channel_pipeline_rules", "bulk_update_m3u_group_settings",
    "import_channels_csv", "match_channels_epg", "match_streams_to_channels",
    "merge_channels", "probe_bulk_streams", "refresh_all_epg", "refresh_all_m3u",
    "update_event_sync_team_aliases",
})

SAFETY_INVENTORY: dict[str, ToolSafety] = {
    name: (
        ToolSafety.READ_ONLY if name in _READ_ONLY_TOOLS
        else ToolSafety.DESTRUCTIVE if name in _DESTRUCTIVE_TOOLS
        else ToolSafety.MUTATING
    )
    for name in _ALL_TOOLS
}

CONFIRMATION_TTL_SECONDS = 300
DESTRUCTIVE_BATCH_HARD_CAP = 500
_SIGNING_KEY = secrets.token_bytes(32)
_LEGACY_CONTENT_GUARDED_TOOLS = frozenset({
    "bulk_delete_channels", "clear_auto_created", "bulk_merge_duplicate_channels",
})


def _canonical_arguments(arguments: dict[str, Any]) -> bytes:
    resolved = {
        key: value for key, value in arguments.items()
        if key not in {"confirmation_token", "confirm", "confirm_apply"}
    }
    return json.dumps(resolved, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def confirmation_token(tool_name: str, arguments: dict[str, Any], *, issued_at: int | None = None) -> str:
    """Create a short-lived token bound to a tool and its resolved arguments."""
    timestamp = int(time.time()) if issued_at is None else int(issued_at)
    payload = str(timestamp).encode() + b"\0" + tool_name.encode() + b"\0" + _canonical_arguments(arguments)
    signature = hmac.new(_SIGNING_KEY, payload, hashlib.sha256).digest()
    encoded = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    return f"v1.{timestamp}.{encoded}"


def _validate_confirmation(tool_name: str, arguments: dict[str, Any], supplied: str) -> str | None:
    try:
        version, raw_timestamp, _ = supplied.split(".", 2)
        timestamp = int(raw_timestamp)
    except (AttributeError, TypeError, ValueError):
        return "invalid"
    if version != "v1":
        return "invalid"
    age = int(time.time()) - timestamp
    if age < 0 or age > CONFIRMATION_TTL_SECONDS:
        return "expired"
    expected = confirmation_token(tool_name, arguments, issued_at=timestamp)
    return None if hmac.compare_digest(supplied, expected) else "mismatch"


def _preview(tool_name: str, arguments: dict[str, Any]) -> str:
    token = confirmation_token(tool_name, arguments)
    targets = _canonical_arguments(arguments).decode()
    return (
        f"PREVIEW ONLY — {tool_name} will run against this resolved target/input set: {targets}\n"
        "No state was changed. Review the set, then repeat the exact call with:\n"
        f"confirmation_token: {token}\n"
        f"The token expires in {CONFIRMATION_TTL_SECONDS} seconds and any input/target drift invalidates it."
    )


def _oversized_batch(arguments: dict[str, Any]) -> tuple[str, int] | None:
    for key, value in arguments.items():
        if isinstance(value, list) and len(value) > DESTRUCTIVE_BATCH_HARD_CAP:
            return key, len(value)
    return None


def install_safety_policy(mcp) -> None:
    """Annotate and guard a complete FastMCP registry, failing closed on drift."""
    tools = mcp._tool_manager._tools
    missing = sorted(set(tools) - set(SAFETY_INVENTORY))
    stale = sorted(set(SAFETY_INVENTORY) - set(tools))
    if missing or stale:
        raise RuntimeError(f"MCP safety inventory mismatch; unclassified={missing}; stale={stale}")

    for name, tool in tools.items():
        classification = SAFETY_INVENTORY[name]
        tool.annotations = ToolAnnotations(
            readOnlyHint=classification is ToolSafety.READ_ONLY,
            destructiveHint=classification is ToolSafety.DESTRUCTIVE,
            idempotentHint=classification is ToolSafety.READ_ONLY,
            openWorldHint=False,
        )
        tool.meta = {**(tool.meta or {}), "ecmSafetyClassification": classification.value}
        if classification is not ToolSafety.DESTRUCTIVE:
            continue

        # These handlers already preview the backend-resolved target ids and
        # enforce caps. Their shared token implementation is expiry-hardened in
        # _guardrails; wrapping them again would create an unusable double gate.
        if name in _LEGACY_CONTENT_GUARDED_TOOLS:
            continue

        # Expose the uniform second-call field in MCP discovery. The wrapper
        # removes it before the original function's Pydantic argument model.
        tool.parameters["properties"]["confirmation_token"] = {
            "type": "string",
            "description": "Expiring content-bound token returned by the mutation-free preview.",
        }
        original_run = tool.run

        async def guarded_run(self, arguments, context=None, convert_result=False, *, _name=name, _run=original_run):
            def present(value: str):
                return self.fn_metadata.convert_result(value) if convert_result else value

            supplied = arguments.get("confirmation_token")
            oversized = _oversized_batch(arguments)
            if oversized:
                field, count = oversized
                return present(
                    f"Refusing destructive batch: {field} contains {count} items; "
                    f"the hard cap is {DESTRUCTIVE_BATCH_HARD_CAP}. Split the operation."
                )
            if not supplied:
                return present(_preview(_name, arguments))
            failure = _validate_confirmation(_name, arguments, supplied)
            if failure == "expired":
                return present("Confirmation token expired; request a new mutation-free preview.")
            if failure:
                return present(
                    "Confirmation does not match the current resolved target/input set; "
                    "request a new preview."
                )
            clean_arguments = {key: value for key, value in arguments.items() if key != "confirmation_token"}
            # Retire older boolean second gates behind the uniform token. The
            # booleans are excluded from token content, so adding them cannot
            # create drift or an accidental third-call confirmation loop.
            if "confirm" in self.parameters["properties"]:
                clean_arguments["confirm"] = True
            if "confirm_apply" in self.parameters["properties"]:
                clean_arguments["confirm_apply"] = True
            return await _run(clean_arguments, context=context, convert_result=convert_result)

        object.__setattr__(tool, "run", MethodType(guarded_run, tool))
