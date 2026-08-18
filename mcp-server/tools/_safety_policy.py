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
import threading
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
    "preview_", "search_",
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
    "update_event_sync_team_aliases", "accept_channel_merge", "dismiss_probe_failures",
    "probe_streams", "run_channel_pipeline", "run_auto_creation",
    "mark_notifications_read", "generate_dummy_epg", "add_tags_to_group",
    "reorder_streams", "set_logo_from_epg", "update_channel",
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
NONCE_LEDGER_MAX_ENTRIES = 256
_SIGNING_KEY = secrets.token_bytes(32)
_LEGACY_CONTENT_GUARDED_TOOLS = frozenset({
    "bulk_delete_channels", "clear_auto_created", "bulk_merge_duplicate_channels",
})

# This is a behavior table, rather than a naming heuristic.  These tools are
# destructive only for the listed argument state; their preview modes remain
# directly callable and are also used to resolve the mutation's actual plan.
_CONDITIONAL_MUTATION: dict[str, tuple[str, Any]] = {
    "run_channel_pipeline": ("dry_run", False),
    "run_auto_creation": ("dry_run", False),
    "match_streams_to_channels": ("apply", True),
    "apply_normalization_to_channels": ("dry_run", False),
}

_NONCE_LEDGER: dict[str, tuple[str, bytes, bytes, int]] = {}
_NONCE_LOCK = threading.Lock()


def _canonical_arguments(arguments: dict[str, Any]) -> bytes:
    resolved = {
        key: value for key, value in arguments.items()
        if key not in {"confirmation_token", "confirm", "confirm_apply", "plan_id", "plan_hash", "plan_phase", "plan_stream_ids", "plan_channel_ids", "plan_notification_ids", "plan_profile_ids", "plan_current_channel", "plan_logo_actions"}
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


def _issue_preview(tool_name: str, arguments: dict[str, Any], resolved: Any) -> str:
    nonce = secrets.token_urlsafe(18)
    timestamp = int(time.time())
    args_bytes = _canonical_arguments(arguments)
    resolved_bytes = json.dumps(
        resolved, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    payload = b"\0".join(
        (str(timestamp).encode(), nonce.encode(), tool_name.encode(), args_bytes, resolved_bytes)
    )
    signature = base64.urlsafe_b64encode(
        hmac.new(_SIGNING_KEY, payload, hashlib.sha256).digest()
    ).decode().rstrip("=")
    token = f"v2.{timestamp}.{nonce}.{signature}"
    with _NONCE_LOCK:
        expired_before = timestamp - CONFIRMATION_TTL_SECONDS
        for old_token, record in list(_NONCE_LEDGER.items()):
            if record[3] < expired_before:
                del _NONCE_LEDGER[old_token]
        while len(_NONCE_LEDGER) >= NONCE_LEDGER_MAX_ENTRIES:
            oldest = min(_NONCE_LEDGER, key=lambda item: _NONCE_LEDGER[item][3])
            del _NONCE_LEDGER[oldest]
        _NONCE_LEDGER[token] = (tool_name, args_bytes, resolved_bytes, timestamp)
    def safe_preview(value: Any, key: str = "") -> Any:
        if isinstance(value, dict):
            return {k: safe_preview(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [safe_preview(item, key) for item in value]
        if isinstance(value, str) and any(marker in key.lower() for marker in ("url", "token", "key", "secret")):
            return f"<redacted sha256:{hashlib.sha256(value.encode()).hexdigest()[:12]}>"
        return value

    targets = json.dumps(safe_preview(resolved), sort_keys=True, separators=(",", ":"))
    return (
        f"PREVIEW ONLY — {tool_name} will run against this resolved target/input set: {targets}\n"
        "No state was changed. Review the set, then repeat the exact call with:\n"
        f"confirmation_token: {token}\n"
        f"The token expires in {CONFIRMATION_TTL_SECONDS} seconds and any input/target drift invalidates it."
    )


def _oversized_batch(arguments: dict[str, Any]) -> tuple[str, int] | None:
    for key, value in arguments.items():
        if isinstance(value, list) and len(value) >= DESTRUCTIVE_BATCH_HARD_CAP:
            return key, len(value)
    return None


def _resolved_count(resolved: Any) -> int:
    if isinstance(resolved, dict):
        candidate_counts = [len(v) for v in resolved.values() if isinstance(v, list)]
        return max(candidate_counts, default=0)
    return len(resolved) if isinstance(resolved, list) else 0


async def _paged_ids(client, endpoint, *, query: dict[str, Any] | None = None) -> list[int]:
    ids: list[int] = []
    page = 1
    while True:
        page_query = {**(query or {}), "page": page, "page_size": 500}
        result = await client.call_endpoint(endpoint, query=page_query)
        rows = (
            result.get("results", result.get("channels", []))
            if isinstance(result, dict) else (result or [])
        )
        ids.extend(
            row["id"] for row in rows
            if isinstance(row, dict) and row.get("id") is not None
        )
        if not isinstance(result, dict) or not result.get("next"):
            return sorted(set(ids))
        page += 1


async def _resolve_targets(name: str, arguments: dict[str, Any], original_run) -> Any:
    """Resolve state-derived selections without invoking a mutating handler."""
    clean = {k: v for k, v in arguments.items() if k != "confirmation_token"}

    if name in {"run_channel_pipeline", "run_auto_creation"}:
        from _endpoint_contracts import ENDPOINTS
        from tools import channel_pipeline
        return await channel_pipeline.get_ecm_client().call_endpoint(
            ENDPOINTS["ac_prepare_run"], body={"dry_run": True}
        )
    if name == "apply_normalization_to_channels":
        from _endpoint_contracts import ENDPOINTS
        from tools import normalization
        return await normalization.get_ecm_client().call_endpoint(
            ENDPOINTS["normalization_apply_to_channels"], query={"dry_run": True},
            body={"actions": clean.get("actions") or []},
        )
    if name == "clear_emby_logos":
        from _endpoint_contracts import ENDPOINTS
        from tools import emby
        types = clean.get("logo_types") or list(emby._VALID_LOGO_TYPES)
        return await emby.get_ecm_client().call_endpoint(
            ENDPOINTS["emby_prepare_clear_logos"], body={"logo_types": types}
        )

    if name == "cleanup_struck_out_streams":
        from _endpoint_contracts import ENDPOINTS
        from tools import streams

        result = await streams.get_ecm_client().call_endpoint(ENDPOINTS["stream_stats_struck_out"])
        rows = result.get("streams", []) if isinstance(result, dict) else (result or [])
        return {
            "stream_ids": sorted({
                r.get("stream_id", r.get("id")) for r in rows
                if r.get("stream_id", r.get("id")) is not None
            }),
            "channel_ids": sorted({
                c.get("id") for r in rows for c in r.get("channels", [])
                if c.get("id") is not None
            }),
            "delete_empty_channels": bool(clean.get("delete_empty_channels")),
        }
    if name == "delete_orphaned_groups":
        from _endpoint_contracts import ENDPOINTS
        from tools import channel_groups
        explicit = clean.get("group_ids")
        if explicit:
            return {"group_ids": sorted(set(explicit))}
        result = await channel_groups.get_ecm_client().call_endpoint(ENDPOINTS["groups_orphaned"])
        rows = result.get("groups", result.get("results", [])) if isinstance(result, dict) else (result or [])
        return {"group_ids": sorted(row["id"] for row in rows if row.get("id") is not None)}
    if name in {"delete_all_notifications", "mark_notifications_read"}:
        from _endpoint_contracts import ENDPOINTS
        from tools import notifications
        client = notifications.get_ecm_client()
        rows: list[dict] = []
        page = 1
        while len(rows) < DESTRUCTIVE_BATCH_HARD_CAP:
            result = await client.call_endpoint(
                ENDPOINTS["notifications_list"],
                query={"page": page, "page_size": 100},
            )
            batch = result.get("notifications", result.get("results", [])) if isinstance(result, dict) else (result or [])
            rows.extend(batch)
            if len(batch) < 100 or (isinstance(result, dict) and len(rows) >= result.get("total", 0)):
                break
            page += 1
        include_unread = bool(clean.get("include_unread"))
        return {"notification_ids": sorted(
            row["id"] for row in rows
            if row.get("id") is not None and (
                (name == "mark_notifications_read" and not row.get("read", row.get("is_read", False)))
                or (name == "delete_all_notifications" and (include_unread or row.get("read", row.get("is_read", False))))
            )
        )}
    if name == "generate_dummy_epg":
        from _endpoint_contracts import ENDPOINTS
        from tools import epg
        result = await epg.get_ecm_client().call_endpoint(ENDPOINTS["dummy_epg_list_profiles"])
        rows = result.get("profiles", result.get("results", [])) if isinstance(result, dict) else (result or [])
        return {"profile_ids": sorted(
            row["id"] for row in rows
            if row.get("id") is not None and row.get("enabled", True)
        )}
    if name in {"reorder_streams", "update_channel"}:
        from _endpoint_contracts import ENDPOINTS
        from tools import channels
        current = await channels.get_ecm_client().call_endpoint(
            ENDPOINTS["channels_get"], path_args={"channel_id": clean["channel_id"]}
        )
        return {"current_channel": current}
    if name == "set_logo_from_epg":
        from tools import channels
        client = channels.get_ecm_client()
        actions = []
        for channel_id in sorted(set(clean.get("channel_ids") or [])):
            channel = await client.get(f"/api/channels/{channel_id}")  # contract-exempt: exact read-set materialization for cross-domain set_logo_from_epg
            epg_data_id = channel.get("epg_data_id")
            epg_entry = await client.get(f"/api/epg/data/{epg_data_id}") if epg_data_id else None  # contract-exempt: exact EPG read paired with channel precondition above
            actions.append({
                "channel_id": channel_id,
                "channel": channel,
                "epg_entry": epg_entry,
                "icon_url": (epg_entry or {}).get("icon_url") or (epg_entry or {}).get("icon"),
            })
        return {"logo_actions": actions}
    if name in {"refresh_all_epg", "match_channels_epg"}:
        from _endpoint_contracts import ENDPOINTS
        from tools import epg

        client = epg.get_ecm_client()
        resolved: dict[str, Any] = {}
        source_ids = (
            clean.get("source_ids") if name == "refresh_all_epg"
            else clean.get("epg_source_ids")
        )
        all_sources_selected = source_ids is None or (name == "match_channels_epg" and not source_ids)
        if all_sources_selected:
            sources = await client.call_endpoint(ENDPOINTS["epg_list_sources"])
            sources = (
                sources.get("sources", sources.get("results", []))
                if isinstance(sources, dict) else (sources or [])
            )
            source_ids = [row.get("id") for row in sources if row.get("id") is not None]
        resolved["epg_source_ids"] = sorted(set(source_ids))
        if name == "match_channels_epg":
            channel_ids = clean.get("channel_ids")
            resolved["channel_ids"] = (
                sorted(set(channel_ids)) if channel_ids
                else await _paged_ids(client, ENDPOINTS["channels_list"])
            )
        return resolved
    if name == "refresh_all_m3u":
        from _endpoint_contracts import ENDPOINTS
        from tools import m3u

        rows = await m3u.get_ecm_client().call_endpoint(ENDPOINTS["m3u_list_providers"])
        return {"account_ids": sorted({
            r.get("id") for r in (rows or []) if r.get("id") is not None
        })}
    if name == "probe_streams":
        from _endpoint_contracts import ENDPOINTS
        from tools import streams

        return {"stream_ids": await _paged_ids(streams.get_ecm_client(), ENDPOINTS["streams_list"])}
    if name in _CONDITIONAL_MUTATION:
        preview_args = dict(clean)
        field, _ = _CONDITIONAL_MUTATION[name]
        preview_args[field] = False
        preview = await original_run(preview_args, context=None, convert_result=False)
        return {"backend_preview": str(preview)}
    return json.loads(_canonical_arguments(clean))


def _consume(token: str, tool_name: str, arguments: dict[str, Any], resolved: Any) -> str | None:
    try:
        _, raw_timestamp, _, _ = token.split(".", 3)
        timestamp = int(raw_timestamp)
    except (AttributeError, TypeError, ValueError):
        return "invalid"
    age = int(time.time()) - timestamp
    if age < 0 or age > CONFIRMATION_TTL_SECONDS:
        with _NONCE_LOCK:
            _NONCE_LEDGER.pop(token, None)
        return "expired"
    args_bytes = _canonical_arguments(arguments)
    resolved_bytes = json.dumps(
        resolved, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    with _NONCE_LOCK:
        record = _NONCE_LEDGER.get(token)
        if record is None:
            return "used"
        if record[:3] != (tool_name, args_bytes, resolved_bytes):
            return "drift"
        del _NONCE_LEDGER[token]
    return None


def _stored_resolution(token: str) -> Any | None:
    """Read the immutable server-plan reference; consumption remains separate."""
    with _NONCE_LOCK:
        record = _NONCE_LEDGER.get(token)
    return json.loads(record[2]) if record is not None else None


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
            conditional = _CONDITIONAL_MUTATION.get(_name)
            if conditional:
                field, mutating_value = conditional
                if arguments.get(field, self.parameters["properties"].get(field, {}).get("default")) != mutating_value:
                    clean_arguments = {
                        key: value for key, value in arguments.items()
                        if key != "confirmation_token"
                    }
                    return await _run(clean_arguments, context=context, convert_result=convert_result)
            oversized = _oversized_batch(arguments)
            if oversized:
                field, count = oversized
                return present(
                    f"Refusing destructive batch: {field} contains {count} items; "
                    f"the hard cap is {DESTRUCTIVE_BATCH_HARD_CAP}. Split the operation."
                )
            if supplied and supplied.startswith("v1."):
                failure = _validate_confirmation(_name, arguments, supplied)
                if failure == "expired":
                    return present("Confirmation token expired; request a new mutation-free preview.")
                return present(
                    "Confirmation does not match the current resolved target/input set; "
                    "request a new preview."
                )
            server_planned = _name in {
                "run_channel_pipeline", "run_auto_creation",
                "apply_normalization_to_channels", "clear_emby_logos",
            }
            resolved = (
                _stored_resolution(supplied)
                if supplied and server_planned
                else await _resolve_targets(_name, arguments, _run)
            )
            if resolved is None:
                return present("Confirmation token was already used or is unknown; request a new preview.")
            resolved_count = _resolved_count(resolved)
            if resolved_count >= DESTRUCTIVE_BATCH_HARD_CAP:
                return present(
                    f"Refusing destructive batch: resolved target set contains {resolved_count} items; "
                    f"the hard cap is {DESTRUCTIVE_BATCH_HARD_CAP}. Narrow the operation."
                )
            if not supplied:
                return present(_issue_preview(_name, arguments, resolved))
            failure = _consume(supplied, _name, arguments, resolved)
            if failure == "expired":
                return present("Confirmation token expired; request a new mutation-free preview.")
            if failure == "used":
                return present(
                    "Confirmation token was already used or is unknown; "
                    "request a new mutation-free preview."
                )
            if failure == "drift":
                return present("Backend target drift detected; request a new mutation-free preview.")
            if failure:
                return present(
                    "Confirmation does not match the current resolved target/input set; "
                    "request a new preview."
                )
            clean_arguments = {key: value for key, value in arguments.items() if key != "confirmation_token"}
            if _name in {"run_channel_pipeline", "run_auto_creation", "apply_normalization_to_channels", "clear_emby_logos"}:
                clean_arguments["plan_id"] = resolved["plan_id"]
                clean_arguments["plan_hash"] = resolved["plan_hash"]
                if _name in {"run_channel_pipeline", "run_auto_creation"}:
                    clean_arguments["plan_phase"] = resolved.get("phase", "execute")
            if _name == "cleanup_struck_out_streams":
                clean_arguments["plan_stream_ids"] = resolved["stream_ids"]
                clean_arguments["plan_channel_ids"] = resolved["channel_ids"]
            if _name in {"delete_all_notifications", "mark_notifications_read"}:
                clean_arguments["plan_notification_ids"] = resolved["notification_ids"]
            if _name == "generate_dummy_epg":
                clean_arguments["plan_profile_ids"] = resolved["profile_ids"]
            if _name in {"reorder_streams", "update_channel"}:
                clean_arguments["plan_current_channel"] = resolved["current_channel"]
            if _name == "set_logo_from_epg":
                clean_arguments["plan_logo_actions"] = resolved["logo_actions"]
            # Retire older boolean second gates behind the uniform token. The
            # booleans are excluded from token content, so adding them cannot
            # create drift or an accidental third-call confirmation loop.
            if "confirm" in self.parameters["properties"]:
                clean_arguments["confirm"] = True
            if "confirm_apply" in self.parameters["properties"]:
                clean_arguments["confirm_apply"] = True
            result = await _run(clean_arguments, context=context, convert_result=convert_result)
            staged_text = result if isinstance(result, str) else None
            if (
                staged_text is None and isinstance(result, tuple) and result
                and isinstance(result[0], list) and result[0]
            ):
                staged_text = getattr(result[0][0], "text", None)
            if (
                _name in {"run_channel_pipeline", "run_auto_creation"}
                and isinstance(staged_text, str) and staged_text.startswith("ECM_STAGED_PLAN:")
            ):
                next_plan = json.loads(staged_text.removeprefix("ECM_STAGED_PLAN:"))
                return present(
                    "Provider refresh completed. A second review is required for the exact "
                    "post-refresh pipeline writes.\n" + _issue_preview(_name, arguments, next_plan)
                )
            return result

        object.__setattr__(tool, "run", MethodType(guarded_run, tool))
