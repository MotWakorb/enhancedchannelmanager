"""Declarative MCP-tool ↔ backend-API endpoint contracts.

This module is the single source of truth that *both* the MCP tools and the
backend-side contract test consume:

* The MCP tools (``mcp-server/tools/*.py``) call backend endpoints **through**
  :func:`ecm_client.ECMClient.call_endpoint`, passing the :class:`Endpoint`
  declared here. ``call_endpoint`` enforces — at call time, always on — that
  the body / query keys a tool sends are a subset of the keys this registry
  declares. A tool that drifts from the registry (e.g. sends ``group_id`` when
  the backend wants ``channel_group_id``) therefore fails *loudly at call time*
  instead of silently at the backend.

* The contract test (``backend/tests/integration/test_mcp_tool_contracts.py``)
  cross-checks every :class:`Endpoint` against the backend's live OpenAPI spec
  (``app.openapi()``): the ``(method, path)`` must exist, ``request_fields``
  must be a subset of the request-body schema's properties (the GH #221
  catcher — ``group_id`` vs ``channel_group_id``), ``query_params`` a subset of
  the declared query parameters, and ``response_fields`` a subset of the 2xx
  response schema's properties (the GH #222 catcher — the ``{"rules": [...]}``
  envelope). When the backend route declares no Pydantic model for its body or
  response (it returns a bare ``dict`` — most ECM routes do), the test can't
  cross-check those names; the call-time subset guard still applies.

**Scope.** As of ``enhancedchannelmanager-vtghg`` Phase 2 this registry covers
*every* MCP tool domain — ``channels`` and ``auto_creation`` (Phase 1) plus
``channel_groups``, ``epg``, ``export``, ``m3u``, ``normalization``,
``notifications``, ``profiles``, ``stats``, ``streams``, ``system`` and
``tasks`` (Phase 2). The contract test's tool-source guard is now FAIL-mode:
any ``client.<verb>("/api/...")`` literal in ``mcp-server/tools/*.py`` or
``resources/*.py`` that hasn't been migrated to ``call_endpoint`` must carry a
``# contract-exempt: <reason>`` comment, or the test fails. A small number of
tools that compose multiple backend calls, build dynamic paths, or send
arbitrary-key bodies (e.g. M3U group-settings) stay on the raw ``client.<verb>``
methods with that marker — intentional, not drift.

When the backend route declares its body via ``request: Request`` (raw, no
Pydantic model) the OpenAPI spec has no request-body schema to cross-check; the
contract test treats those like the free-object case (the call-time subset
guard in ``call_endpoint`` still constrains the tools).

**Imports.** Pure stdlib + ``dataclasses`` only — this module is imported by the
backend test, whose venv does not have ``httpx`` or the ``mcp`` SDK.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Endpoint:
    """One backend endpoint an MCP tool calls, with the keys it touches.

    Only *names* are stored — not full schemas — because that is all the
    subset checks (call-time guard + contract test) need.

    Attributes:
        name: Stable id for this endpoint (also the ``ENDPOINTS`` key).
        method: HTTP verb, uppercase (``GET``/``POST``/``PATCH``/``PUT``/``DELETE``).
        path: FastAPI-style path with ``{placeholder}`` segments, e.g.
            ``/api/channels/{channel_id}``.
        request_fields: Body keys a tool may send. Empty for GET/DELETE with no
            body. For endpoints whose backend body is a free-form ``dict``
            (no Pydantic model), this is the *known* set the tools actually
            send — the contract test skips the cross-check against the backend
            schema in that case, but the call-time guard still applies.
        query_params: Query-string keys a tool may send.
        response_fields: Response keys a tool reads. Usually empty: most ECM
            routes return a bare ``dict`` with no ``response_model``, so the
            OpenAPI response schema declares no properties to check against.
        response_is_list: True if the 2xx response is a JSON array (not an
            object). The contract test asserts the OpenAPI response schema's
            ``type`` matches.
        exempt_reason: Escape hatch — if set, the contract test skips this
            endpoint entirely. Unused in Phase 1 (all channels/auto_creation
            endpoints are modelled); tools that genuinely can't be expressed as
            one ``Endpoint`` stay on the raw ``client.<verb>`` methods with a
            ``# contract-exempt:`` comment instead.
    """

    name: str
    method: str
    path: str
    request_fields: frozenset[str] = field(default_factory=frozenset)
    query_params: frozenset[str] = field(default_factory=frozenset)
    response_fields: frozenset[str] = field(default_factory=frozenset)
    response_is_list: bool = False
    exempt_reason: str | None = None


# ---------------------------------------------------------------------------
# Field-name groups reused below (kept in sync with the backend Pydantic models
# in backend/routers/channels.py and backend/routers/auto_creation.py).
# ---------------------------------------------------------------------------

# backend/routers/channels.py :: PATCH /api/channels/{id} takes ``data: dict``
# (free-form, forwarded to Dispatcharr). These are the channel fields the MCP
# tools actually PATCH — the call-time guard validates against this set; the
# contract test can't cross-check it (the backend body schema has no
# properties).
_CHANNEL_PATCH_FIELDS = frozenset(
    {"name", "channel_number", "channel_group_id", "tvg_id", "logo_id", "streams"}
)

# backend/routers/auto_creation.py :: CreateAutoCreationRuleRequest
_AC_RULE_CREATE_FIELDS = frozenset(
    {
        "name",
        "description",
        "enabled",
        "priority",
        "m3u_account_id",
        "target_group_id",
        "conditions",
        "actions",
        "run_on_refresh",
        "stop_on_first_match",
        "sort_field",
        "sort_order",
        "probe_on_sort",
        "sort_regex",
        "stream_sort_field",
        "stream_sort_order",
        "quality_tie_break_order",
        "quality_m3u_tie_break_enabled",
        "normalization_group_ids",
        "skip_struck_streams",
        "orphan_action",
        "match_scope_target_group",
    }
)

# backend/routers/auto_creation.py :: UpdateAutoCreationRuleRequest — same
# field names as create, all Optional.
_AC_RULE_UPDATE_FIELDS = _AC_RULE_CREATE_FIELDS


ENDPOINTS: dict[str, Endpoint] = {
    # -- channels domain ----------------------------------------------------
    "channels_list": Endpoint(
        name="channels_list",
        method="GET",
        path="/api/channels",
        query_params=frozenset({"page", "page_size", "search", "channel_group"}),
    ),
    "channels_get": Endpoint(
        name="channels_get",
        method="GET",
        path="/api/channels/{channel_id}",
    ),
    "channels_create": Endpoint(
        name="channels_create",
        method="POST",
        path="/api/channels",
        request_fields=frozenset(
            {"name", "channel_number", "channel_group_id", "logo_id", "tvg_id", "normalize"}
        ),
    ),
    "channels_update": Endpoint(
        name="channels_update",
        method="PATCH",
        path="/api/channels/{channel_id}",
        # Backend body is ``data: dict`` (free-form) — see _CHANNEL_PATCH_FIELDS.
        request_fields=_CHANNEL_PATCH_FIELDS,
    ),
    "channels_delete": Endpoint(
        name="channels_delete",
        method="DELETE",
        path="/api/channels/{channel_id}",
    ),
    "channels_add_stream": Endpoint(
        name="channels_add_stream",
        method="POST",
        path="/api/channels/{channel_id}/add-stream",
        request_fields=frozenset({"stream_id"}),
    ),
    "channels_add_streams": Endpoint(
        name="channels_add_streams",
        method="POST",
        path="/api/channels/{channel_id}/add-streams",
        request_fields=frozenset({"stream_ids"}),
    ),
    "channels_remove_stream": Endpoint(
        name="channels_remove_stream",
        method="POST",
        path="/api/channels/{channel_id}/remove-stream",
        request_fields=frozenset({"stream_id"}),
    ),
    "channels_reorder_streams": Endpoint(
        name="channels_reorder_streams",
        method="POST",
        path="/api/channels/{channel_id}/reorder-streams",
        request_fields=frozenset({"stream_ids"}),
    ),
    "channels_assign_numbers": Endpoint(
        name="channels_assign_numbers",
        method="POST",
        path="/api/channels/assign-numbers",
        request_fields=frozenset({"channel_ids", "starting_number"}),
    ),
    "channels_clear_auto_created": Endpoint(
        name="channels_clear_auto_created",
        method="POST",
        path="/api/channels/clear-auto-created",
        request_fields=frozenset({"group_ids"}),
    ),
    "channels_find_duplicates": Endpoint(
        name="channels_find_duplicates",
        method="POST",
        path="/api/channels/find-duplicates",
    ),
    "channels_bulk_merge": Endpoint(
        name="channels_bulk_merge",
        method="POST",
        path="/api/channels/bulk-merge",
        request_fields=frozenset({"merges"}),
    ),
    "channels_bulk_commit": Endpoint(
        name="channels_bulk_commit",
        method="POST",
        path="/api/channels/bulk-commit",
        # Top-level wrapper keys of BulkCommitRequest. The per-operation
        # discriminated union lives *inside* ``operations`` (a list), so the
        # contract test only needs to check these top-level names.
        request_fields=frozenset(
            {"operations", "groupsToCreate", "validateOnly", "continueOnError", "consolidate"}
        ),
    ),
    # -- auto_creation domain ----------------------------------------------
    "ac_list_rules": Endpoint(
        name="ac_list_rules",
        method="GET",
        path="/api/auto-creation/rules",
    ),
    "ac_get_rule": Endpoint(
        name="ac_get_rule",
        method="GET",
        path="/api/auto-creation/rules/{rule_id}",
    ),
    "ac_create_rule": Endpoint(
        name="ac_create_rule",
        method="POST",
        path="/api/auto-creation/rules",
        request_fields=_AC_RULE_CREATE_FIELDS,
    ),
    "ac_update_rule": Endpoint(
        name="ac_update_rule",
        method="PUT",
        path="/api/auto-creation/rules/{rule_id}",
        request_fields=_AC_RULE_UPDATE_FIELDS,
    ),
    "ac_delete_rule": Endpoint(
        name="ac_delete_rule",
        method="DELETE",
        path="/api/auto-creation/rules/{rule_id}",
    ),
    "ac_toggle_rule": Endpoint(
        name="ac_toggle_rule",
        method="POST",
        path="/api/auto-creation/rules/{rule_id}/toggle",
    ),
    "ac_duplicate_rule": Endpoint(
        name="ac_duplicate_rule",
        method="POST",
        path="/api/auto-creation/rules/{rule_id}/duplicate",
    ),
    "ac_analyze_rules": Endpoint(
        name="ac_analyze_rules",
        method="POST",
        path="/api/auto-creation/rules/analyze",
    ),
    "ac_run": Endpoint(
        name="ac_run",
        method="POST",
        path="/api/auto-creation/run",
        request_fields=frozenset({"dry_run", "m3u_account_ids", "rule_ids"}),
    ),
    "ac_list_executions": Endpoint(
        name="ac_list_executions",
        method="GET",
        path="/api/auto-creation/executions",
        query_params=frozenset({"limit", "offset", "rule_id", "status"}),
    ),
    "ac_rollback": Endpoint(
        name="ac_rollback",
        method="POST",
        path="/api/auto-creation/executions/{execution_id}/rollback",
    ),
    # -- channel_groups domain --------------------------------------------
    "groups_list": Endpoint(
        name="groups_list",
        method="GET",
        path="/api/channel-groups",
    ),
    "groups_create": Endpoint(
        name="groups_create",
        method="POST",
        path="/api/channel-groups",
        request_fields=frozenset({"name"}),  # CreateChannelGroupRequest
    ),
    "groups_delete": Endpoint(
        name="groups_delete",
        method="DELETE",
        path="/api/channel-groups/{group_id}",
    ),
    "groups_orphaned": Endpoint(
        name="groups_orphaned",
        method="GET",
        path="/api/channel-groups/orphaned",
    ),
    "groups_delete_orphaned": Endpoint(
        name="groups_delete_orphaned",
        method="DELETE",
        path="/api/channel-groups/orphaned",
        request_fields=frozenset({"group_ids"}),  # DeleteOrphanedGroupsRequest
    ),
    "groups_hidden": Endpoint(
        name="groups_hidden",
        method="GET",
        path="/api/channel-groups/hidden",
    ),
    "groups_auto_created": Endpoint(
        name="groups_auto_created",
        method="GET",
        path="/api/channel-groups/auto-created",
    ),
    "groups_with_streams": Endpoint(
        name="groups_with_streams",
        method="GET",
        path="/api/channel-groups/with-streams",
    ),
    # -- epg domain --------------------------------------------------------
    "epg_list_sources": Endpoint(
        name="epg_list_sources",
        method="GET",
        path="/api/epg/sources",
    ),
    "epg_create_source": Endpoint(
        name="epg_create_source",
        method="POST",
        path="/api/epg/sources",
        # Backend body is ``request: Request`` (raw) — forwarded to Dispatcharr.
        # These are the keys the tool sends; the call-time guard validates them.
        request_fields=frozenset({"name", "url"}),
    ),
    "epg_update_source": Endpoint(
        name="epg_update_source",
        method="PATCH",
        path="/api/epg/sources/{source_id}",
        # Backend body is ``request: Request`` (raw) — see epg_create_source.
        request_fields=frozenset({"name", "url"}),
    ),
    "epg_delete_source": Endpoint(
        name="epg_delete_source",
        method="DELETE",
        path="/api/epg/sources/{source_id}",
    ),
    "epg_refresh_source": Endpoint(
        name="epg_refresh_source",
        method="POST",
        path="/api/epg/sources/{source_id}/refresh",
    ),
    "epg_match": Endpoint(
        name="epg_match",
        method="POST",
        path="/api/epg/match",
        request_fields=frozenset({"channel_ids", "epg_source_ids", "source_order"}),  # EPGMatchRequest
    ),
    "epg_grid": Endpoint(
        name="epg_grid",
        method="GET",
        path="/api/epg/grid",
        query_params=frozenset({"start", "end"}),
    ),
    "dummy_epg_list_profiles": Endpoint(
        name="dummy_epg_list_profiles",
        method="GET",
        path="/api/dummy-epg/profiles",
    ),
    "dummy_epg_generate": Endpoint(
        name="dummy_epg_generate",
        method="POST",
        path="/api/dummy-epg/generate",
    ),
    # -- export domain -----------------------------------------------------
    "export_list_profiles": Endpoint(
        name="export_list_profiles",
        method="GET",
        path="/api/export/profiles",
    ),
    "export_create_profile": Endpoint(
        name="export_create_profile",
        method="POST",
        path="/api/export/profiles",
        # ProfileCreateRequest — full field set; the tool sends only {"name"}.
        request_fields=frozenset(
            {
                "name",
                "description",
                "selection_mode",
                "selected_groups",
                "selected_channels",
                "stream_url_mode",
                "include_logos",
                "include_epg_ids",
                "include_channel_numbers",
                "sort_order",
                "filename_prefix",
            }
        ),
    ),
    "export_delete_profile": Endpoint(
        name="export_delete_profile",
        method="DELETE",
        path="/api/export/profiles/{profile_id}",
    ),
    "export_generate_profile": Endpoint(
        name="export_generate_profile",
        method="POST",
        path="/api/export/profiles/{profile_id}/generate",
    ),
    "export_list_cloud_targets": Endpoint(
        name="export_list_cloud_targets",
        method="GET",
        path="/api/export/cloud-targets",
    ),
    "export_publish_config": Endpoint(
        name="export_publish_config",
        method="POST",
        path="/api/export/publish-configs/{config_id}/publish",
    ),
    # -- m3u domain --------------------------------------------------------
    "m3u_list_providers": Endpoint(
        name="m3u_list_providers",
        method="GET",
        path="/api/providers",
    ),
    "m3u_get_account": Endpoint(
        name="m3u_get_account",
        method="GET",
        path="/api/m3u/accounts/{account_id}",
    ),
    "m3u_create_account": Endpoint(
        name="m3u_create_account",
        method="POST",
        path="/api/m3u/accounts",
        # Backend body is ``request: Request`` (raw) — forwarded to Dispatcharr.
        request_fields=frozenset({"name", "url", "server_type", "server_url"}),
    ),
    "m3u_update_account": Endpoint(
        name="m3u_update_account",
        method="PATCH",
        path="/api/m3u/accounts/{account_id}",
        # Backend body is ``request: Request`` (raw) — forwarded to Dispatcharr.
        request_fields=frozenset({"name", "url", "server_url", "is_active"}),
    ),
    "m3u_delete_account": Endpoint(
        name="m3u_delete_account",
        method="DELETE",
        path="/api/m3u/accounts/{account_id}",
    ),
    "m3u_refresh_all": Endpoint(
        name="m3u_refresh_all",
        method="POST",
        path="/api/m3u/refresh",
    ),
    "m3u_refresh_account": Endpoint(
        name="m3u_refresh_account",
        method="POST",
        path="/api/m3u/refresh/{account_id}",
    ),
    # -- normalization domain ---------------------------------------------
    "normalization_test_batch": Endpoint(
        name="normalization_test_batch",
        method="POST",
        path="/api/normalization/test-batch",
        request_fields=frozenset({"texts"}),  # TestRulesBatchRequest
    ),
    "normalization_list_groups": Endpoint(
        name="normalization_list_groups",
        method="GET",
        path="/api/normalization/groups",
    ),
    # -- notifications domain ---------------------------------------------
    "notifications_list": Endpoint(
        name="notifications_list",
        method="GET",
        path="/api/notifications",
        query_params=frozenset({"page", "page_size", "unread_only", "notification_type"}),
    ),
    "notifications_mark_all_read": Endpoint(
        name="notifications_mark_all_read",
        method="PATCH",
        path="/api/notifications/mark-all-read",
    ),
    "notifications_delete_all": Endpoint(
        name="notifications_delete_all",
        method="DELETE",
        path="/api/notifications",
    ),
    "alert_methods_list": Endpoint(
        name="alert_methods_list",
        method="GET",
        path="/api/alert-methods",
    ),
    "alert_methods_test": Endpoint(
        name="alert_methods_test",
        method="POST",
        path="/api/alert-methods/{method_id}/test",
    ),
    # -- profiles domain ---------------------------------------------------
    "channel_profiles_list": Endpoint(
        name="channel_profiles_list",
        method="GET",
        path="/api/channel-profiles",
    ),
    "stream_profiles_list": Endpoint(
        name="stream_profiles_list",
        method="GET",
        path="/api/stream-profiles",
    ),
    "channel_profiles_bulk_update": Endpoint(
        name="channel_profiles_bulk_update",
        method="PATCH",
        path="/api/channel-profiles/{profile_id}/channels/bulk-update",
        # Backend body is ``request: Request`` (raw) — forwarded to Dispatcharr.
        request_fields=frozenset({"channel_ids", "enabled"}),
    ),
    # -- stats domain ------------------------------------------------------
    "stats_channels": Endpoint(
        name="stats_channels",
        method="GET",
        path="/api/stats/channels",
    ),
    "stats_top_watched": Endpoint(
        name="stats_top_watched",
        method="GET",
        path="/api/stats/top-watched",
        query_params=frozenset({"limit", "sort_by"}),
    ),
    "stats_bandwidth": Endpoint(
        name="stats_bandwidth",
        method="GET",
        path="/api/stats/bandwidth",
    ),
    "stats_popularity_rankings": Endpoint(
        name="stats_popularity_rankings",
        method="GET",
        path="/api/stats/popularity/rankings",
        query_params=frozenset({"limit", "offset"}),
    ),
    "stats_watch_history": Endpoint(
        name="stats_watch_history",
        method="GET",
        path="/api/stats/watch-history",
        query_params=frozenset({"page", "page_size", "channel_id", "ip_address", "days"}),
    ),
    "stats_unique_viewers": Endpoint(
        name="stats_unique_viewers",
        method="GET",
        path="/api/stats/unique-viewers",
        query_params=frozenset({"days"}),
    ),
    "stats_unique_viewers_by_channel": Endpoint(
        name="stats_unique_viewers_by_channel",
        method="GET",
        path="/api/stats/unique-viewers-by-channel",
        query_params=frozenset({"days", "limit"}),
    ),
    "stream_stats_compute_sort": Endpoint(
        name="stream_stats_compute_sort",
        method="POST",
        path="/api/stream-stats/compute-sort",
        request_fields=frozenset({"channels", "mode"}),  # ComputeSortRequest
        response_fields=frozenset({"results"}),  # ComputeSortResponse
    ),
    # -- streams domain ----------------------------------------------------
    "streams_list": Endpoint(
        name="streams_list",
        method="GET",
        path="/api/streams",
        query_params=frozenset(
            {"page", "page_size", "search", "channel_group_name", "m3u_account", "sort", "enrich", "bypass_cache"}
        ),
    ),
    "streams_by_ids": Endpoint(
        name="streams_by_ids",
        method="POST",
        path="/api/streams/by-ids",
        request_fields=frozenset({"stream_ids"}),  # BulkStreamIdsRequest
    ),
    "stream_stats_summary": Endpoint(
        name="stream_stats_summary",
        method="GET",
        path="/api/stream-stats/summary",
    ),
    "stream_stats_probe_all": Endpoint(
        name="stream_stats_probe_all",
        method="POST",
        path="/api/stream-stats/probe/all",
    ),
    "stream_stats_probe_progress": Endpoint(
        name="stream_stats_probe_progress",
        method="GET",
        path="/api/stream-stats/probe/progress",
    ),
    "stream_stats_probe_one": Endpoint(
        name="stream_stats_probe_one",
        method="POST",
        path="/api/stream-stats/probe/{stream_id}",
    ),
    "stream_stats_probe_bulk": Endpoint(
        name="stream_stats_probe_bulk",
        method="POST",
        path="/api/stream-stats/probe/bulk",
        request_fields=frozenset({"stream_ids"}),  # BulkProbeRequest
    ),
    "stream_stats_probe_cancel": Endpoint(
        name="stream_stats_probe_cancel",
        method="POST",
        path="/api/stream-stats/probe/cancel",
    ),
    "stream_stats_probe_results": Endpoint(
        name="stream_stats_probe_results",
        method="GET",
        path="/api/stream-stats/probe/results",
    ),
    "stream_stats_struck_out": Endpoint(
        name="stream_stats_struck_out",
        method="GET",
        path="/api/stream-stats/struck-out",
    ),
    "stream_stats_struck_out_remove": Endpoint(
        name="stream_stats_struck_out_remove",
        method="POST",
        path="/api/stream-stats/struck-out/remove",
        request_fields=frozenset({"stream_ids"}),  # RemoveStruckOutRequest
    ),
    "channels_streams": Endpoint(
        name="channels_streams",
        method="GET",
        path="/api/channels/{channel_id}/streams",
    ),
    # -- system domain -----------------------------------------------------
    "settings_get": Endpoint(
        name="settings_get",
        method="GET",
        path="/api/settings",
    ),
    "backup_create": Endpoint(
        name="backup_create",
        method="GET",
        path="/api/backup/create",
    ),
    "backup_export_sections": Endpoint(
        name="backup_export_sections",
        method="GET",
        path="/api/backup/export-sections",
    ),
    "backup_list_saved": Endpoint(
        name="backup_list_saved",
        method="GET",
        path="/api/backup/saved",
    ),
    "backup_delete_saved": Endpoint(
        name="backup_delete_saved",
        method="DELETE",
        path="/api/backup/saved/{filename}",
    ),
    "journal_list": Endpoint(
        name="journal_list",
        method="GET",
        path="/api/journal",
        query_params=frozenset(
            {"page", "page_size", "category", "action_type", "date_from", "date_to", "search", "user_initiated", "batch_id"}
        ),
    ),
    # -- tasks domain ------------------------------------------------------
    "tasks_list": Endpoint(
        name="tasks_list",
        method="GET",
        path="/api/tasks",
    ),
    "tasks_run": Endpoint(
        name="tasks_run",
        method="POST",
        path="/api/tasks/{task_id}/run",
        request_fields=frozenset({"schedule_id", "parameters"}),  # TaskRunRequest (optional)
    ),
    "tasks_cancel": Endpoint(
        name="tasks_cancel",
        method="POST",
        path="/api/tasks/{task_id}/cancel",
    ),
    "tasks_history": Endpoint(
        name="tasks_history",
        method="GET",
        path="/api/tasks/{task_id}/history",
        query_params=frozenset({"limit", "offset"}),
    ),
    "tasks_history_all": Endpoint(
        name="tasks_history_all",
        method="GET",
        path="/api/tasks/history/all",
        query_params=frozenset({"limit", "offset"}),
    ),
    "tasks_list_schedules": Endpoint(
        name="tasks_list_schedules",
        method="GET",
        path="/api/tasks/{task_id}/schedules",
    ),
    "tasks_create_schedule": Endpoint(
        name="tasks_create_schedule",
        method="POST",
        path="/api/tasks/{task_id}/schedules",
        # TaskScheduleCreate — full field set; the tool sends a subset.
        request_fields=frozenset(
            {
                "name",
                "enabled",
                "schedule_type",
                "interval_seconds",
                "schedule_time",
                "timezone",
                "days_of_week",
                "day_of_month",
                "parameters",
            }
        ),
    ),
    "tasks_delete_schedule": Endpoint(
        name="tasks_delete_schedule",
        method="DELETE",
        path="/api/tasks/{task_id}/schedules/{schedule_id}",
    ),
}
