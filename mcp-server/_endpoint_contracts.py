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
        # GH #801 / bead 0fn69: the tool signatures already forward this and the
        # backend PUT accepts it, but omitting it here made call_endpoint reject
        # the request client-side, which left the documented churn workaround
        # unreachable through the sidecar.
        "allow_manual_channel_merge",
    }
)

# backend/routers/auto_creation.py :: UpdateAutoCreationRuleRequest — same
# field names as create, all Optional.
_AC_RULE_UPDATE_FIELDS = _AC_RULE_CREATE_FIELDS

# Rule fields the backend's Create/UpdateChannelPipelineRuleRequest models
# accept but the MCP sidecar deliberately does NOT expose: no tool signature
# takes them, so declaring them above would advertise keys no tool can send.
#
# This set is the documented half of the GH #801 / bead 0fn69 guard. The
# contract test (backend/tests/integration/test_mcp_tool_contracts.py) asserts
# that every field the backend rule body accepts is either in
# _AC_RULE_CREATE_FIELDS or named here, so a newly added backend field cannot
# go silently unreachable through the sidecar the way
# allow_manual_channel_merge did. Adding a name here is a decision, not a
# formality: it means "the sidecar cannot set this field".
#
#   active_from / active_until  Rule activation window. Date-typed scheduling,
#                               no MCP tool parameter.
#   match_scope_group_id        Explicit merge-lookup scope group (GH #298).
#                               Set through the UI; the sidecar exposes only
#                               the match_scope_target_group boolean.
#   fold_match_key              Fold-match opt-in (GH #645).
#   event_sync_config           Event Sync rule config. A nested object edited
#                               through its own preview/config tooling, not a
#                               scalar the rule tools set.
AC_RULE_FIELDS_NOT_EXPOSED = frozenset(
    {
        "active_from",
        "active_until",
        "match_scope_group_id",
        "fold_match_key",
        "event_sync_config",
    }
)

# backend/routers/dummy_epg.py :: ProfileCreateRequest / ProfileUpdateRequest —
# identical field names between create and update (update makes every field
# Optional; a profile's name IS mutable via PATCH, unlike a channel-pipeline
# rule's group). enhancedchannelmanager-omxy5.
_DUMMY_EPG_PROFILE_FIELDS = frozenset({
    "name", "enabled", "name_source", "stream_index",
    "title_pattern", "time_pattern", "date_pattern",
    "substitution_pairs", "title_template", "description_template",
    "upcoming_title_template", "upcoming_description_template",
    "ended_title_template", "ended_description_template",
    "fallback_title_template", "fallback_description_template",
    "event_timezone", "output_timezone", "program_duration",
    "categories", "channel_logo_url_template", "program_poster_url_template",
    "tvg_id_template", "include_date_tag", "include_live_tag", "include_new_tag",
    "pattern_builder_examples", "pattern_variants", "channel_group_ids",
})


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
        # enhancedchannelmanager-uahp6: optional scope. Absent/None body =
        # global scan (backward compatible); present = scoped to those ids.
        request_fields=frozenset({"channel_ids"}),
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
    # -- enhancedchannelmanager-l3jf7: channel CSV export/import/preview ---
    # export-csv (GET, returns text/csv, not JSON) and import-csv (POST,
    # multipart/form-data upload) are NOT modeled here — call_endpoint only
    # supports JSON-body endpoints. Those two stay on ecm_client.get_text /
    # post_multipart directly with a `# contract-exempt:` comment.
    "channels_preview_csv": Endpoint(
        name="channels_preview_csv",
        method="POST",
        path="/api/channels/preview-csv",
        request_fields=frozenset({"content"}),
        response_fields=frozenset({"rows", "errors"}),
    ),
    # -- enhancedchannelmanager-twadj: logo management ----------------------
    "channels_list_logos": Endpoint(
        name="channels_list_logos",
        method="GET",
        path="/api/channels/logos",
        query_params=frozenset({"page", "page_size", "search"}),
    ),
    "channels_get_logo": Endpoint(
        name="channels_get_logo",
        method="GET",
        path="/api/channels/logos/{logo_id}",
        response_fields=frozenset({"id", "name", "url", "channel_count"}),
    ),
    "channels_create_logo": Endpoint(
        name="channels_create_logo",
        method="POST",
        path="/api/channels/logos",
        request_fields=frozenset({"name", "url"}),  # CreateLogoRequest
    ),
    "channels_update_logo": Endpoint(
        name="channels_update_logo",
        method="PATCH",
        path="/api/channels/logos/{logo_id}",
        # Backend body is ``data: dict`` (free-form) — the tool sends only these.
        request_fields=frozenset({"name", "url"}),
    ),
    "channels_delete_logo": Endpoint(
        name="channels_delete_logo",
        method="DELETE",
        path="/api/channels/logos/{logo_id}",
    ),
    # -- channel_pipeline domain (formerly "auto_creation") ----------------
    "ac_list_rules": Endpoint(
        name="ac_list_rules",
        method="GET",
        path="/api/channel-pipeline/rules",
    ),
    "ac_get_rule": Endpoint(
        name="ac_get_rule",
        method="GET",
        path="/api/channel-pipeline/rules/{rule_id}",
    ),
    "ac_create_rule": Endpoint(
        name="ac_create_rule",
        method="POST",
        path="/api/channel-pipeline/rules",
        request_fields=_AC_RULE_CREATE_FIELDS,
    ),
    "ac_update_rule": Endpoint(
        name="ac_update_rule",
        method="PUT",
        path="/api/channel-pipeline/rules/{rule_id}",
        request_fields=_AC_RULE_UPDATE_FIELDS,
    ),
    "ac_delete_rule": Endpoint(
        name="ac_delete_rule",
        method="DELETE",
        path="/api/channel-pipeline/rules/{rule_id}",
    ),
    "ac_toggle_rule": Endpoint(
        name="ac_toggle_rule",
        method="POST",
        path="/api/channel-pipeline/rules/{rule_id}/toggle",
    ),
    "ac_duplicate_rule": Endpoint(
        name="ac_duplicate_rule",
        method="POST",
        path="/api/channel-pipeline/rules/{rule_id}/duplicate",
    ),
    "ac_analyze_rules": Endpoint(
        name="ac_analyze_rules",
        method="POST",
        path="/api/channel-pipeline/rules/analyze",
    ),
    "ac_run": Endpoint(
        name="ac_run",
        method="POST",
        path="/api/channel-pipeline/run",
        request_fields=frozenset({"dry_run", "m3u_account_ids", "rule_ids"}),
    ),
    # enhancedchannelmanager-jnzst Component B: no-write scored fuzzy preview.
    # The MCP preview_fuzzy_matches tool + the dry-run path of
    # match_streams_to_channels / fuzzy_match_stream read from here.
    "ac_fuzzy_preview": Endpoint(
        name="ac_fuzzy_preview",
        method="GET",
        path="/api/channel-pipeline/fuzzy-preview",
        query_params=frozenset(
            {"group_ids", "min_score", "allow_no_callsign", "page", "page_size"}
        ),
    ),
    # enhancedchannelmanager-ti939.1.4: Event Sync Phase 1A dry-run preview.
    # ZERO writes — the preview_event_sync tool reads from here only.
    "ac_event_sync_preview": Endpoint(
        name="ac_event_sync_preview",
        method="POST",
        path="/api/channel-pipeline/event-sync-preview",
        request_fields=frozenset({"rule_id", "event_sync_config"}),
    ),
    "ac_list_executions": Endpoint(
        name="ac_list_executions",
        method="GET",
        path="/api/channel-pipeline/executions",
        query_params=frozenset({"limit", "offset", "rule_id", "status"}),
    ),
    "ac_get_execution": Endpoint(
        name="ac_get_execution",
        method="GET",
        path="/api/channel-pipeline/executions/{execution_id}",
        # query params the poll uses; include_entities and include_log default
        # to False so we omit them from the poll call (saves DB work).
        query_params=frozenset({"include_entities", "include_log"}),
        # Fields the run_channel_pipeline tool reads from the execution row.
        response_fields=frozenset({
            "id", "status", "mode", "streams_evaluated", "streams_matched",
            "channels_created", "channels_updated", "groups_created",
            "streams_skipped", "duration_seconds", "error_message",
            "dry_run_results",
        }),
    ),
    "ac_rollback": Endpoint(
        name="ac_rollback",
        method="POST",
        path="/api/channel-pipeline/executions/{execution_id}/rollback",
    ),
    # ADR-010 §D8 / uc51o.4 — full whole-run snapshot restore. ``confirm`` is a
    # QUERY param (FastAPI ``Query``), the API-level acknowledgement of the §D5
    # optimistic-overwrite warning; the restore is refused (400) without it. The
    # restore_auto_creation_snapshot MCP tool (uc51o.6) routes here.
    "ac_restore_snapshot": Endpoint(
        name="ac_restore_snapshot",
        method="POST",
        path="/api/channel-pipeline/executions/{execution_id}/restore-snapshot",
        query_params=frozenset({"confirm"}),
    ),
    # ADR-010 §D6 — read-only pre-run snapshot for an execution. The
    # restore_auto_creation_snapshot tool reads ``channel_count`` from here on
    # the confirm=False warning path so the operator sees the blast radius
    # (how many channels a restore would overwrite) before re-invoking.
    "ac_get_execution_snapshot": Endpoint(
        name="ac_get_execution_snapshot",
        method="GET",
        path="/api/channel-pipeline/executions/{execution_id}/snapshot",
        response_fields=frozenset({
            "id", "execution_id", "snapshot_time", "channel_count", "channels",
        }),
    ),
    # -- event_sync_exclusions (bead ti939.3.5) ----------------------------
    # Operator "never attach this provider stream to that event" standing
    # orders. Rows key on content fingerprints (rule_id, provider_id,
    # stream_name_hash, event_key) — NEVER stream/channel ids (both churn),
    # so an exclusion survives provider refreshes by construction. The
    # shared resolver consults these BEFORE the attach band on every run
    # and preview; an exclusion outranks a prior review-queue accept.
    "es_list_exclusions": Endpoint(
        name="es_list_exclusions",
        method="GET",
        path="/api/event-sync-exclusions",
        query_params=frozenset({"rule_id", "page", "page_size"}),
        response_fields=frozenset({
            "exclusions", "total", "page", "page_size", "total_pages",
        }),
    ),
    "es_create_exclusion": Endpoint(
        name="es_create_exclusion",
        method="POST",
        path="/api/event-sync-exclusions",
        request_fields=frozenset({
            "rule_id", "provider_id", "stream_name_hash", "event_key",
            "note", "evidence",
        }),
        response_fields=frozenset({
            "id", "rule_id", "provider_id", "stream_name_hash", "event_key",
            "created_at", "note", "evidence", "already_existed",
        }),
    ),
    "es_delete_exclusion": Endpoint(
        name="es_delete_exclusion",
        method="DELETE",
        path="/api/event-sync-exclusions/{exclusion_id}",
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
        # source_type is required by Dispatcharr (bd-1wq7z.9: omitting it → 400).
        # Schedules Direct sources send username/password instead of url.
        request_fields=frozenset({
            "name", "url", "source_type", "username", "password",
            "is_active", "refresh_interval", "priority", "custom_properties",
        }),
    ),
    "epg_update_source": Endpoint(
        name="epg_update_source",
        method="PATCH",
        path="/api/epg/sources/{source_id}",
        # Backend body is ``request: Request`` (raw) — see epg_create_source.
        request_fields=frozenset({
            "name", "url", "username", "password",
            "is_active", "refresh_interval", "priority", "custom_properties",
        }),
    ),
    # -- Schedules Direct (SD) account lineup management -------------------
    "epg_sd_lineups_list": Endpoint(
        name="epg_sd_lineups_list",
        method="GET",
        path="/api/epg/sources/{source_id}/sd-lineups",
    ),
    "epg_sd_lineup_add": Endpoint(
        name="epg_sd_lineup_add",
        method="POST",
        path="/api/epg/sources/{source_id}/sd-lineups",
        request_fields=frozenset({"lineup"}),  # SDLineupRequest
    ),
    "epg_sd_lineup_remove": Endpoint(
        name="epg_sd_lineup_remove",
        method="DELETE",
        path="/api/epg/sources/{source_id}/sd-lineups",
        request_fields=frozenset({"lineup"}),  # SDLineupRequest
    ),
    "epg_sd_lineups_search": Endpoint(
        name="epg_sd_lineups_search",
        method="POST",
        path="/api/epg/sources/{source_id}/sd-lineups/search",
        request_fields=frozenset({"country", "postalcode"}),  # SDLineupSearchRequest
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
        # "source_order" is DEPRECATED (v0.18.x): EPG source priority is now
        # resolved server-side. The MCP tool no longer sends it, but the field
        # stays in this contract for the deprecation window because the backend
        # EPGMatchRequest still declares it (accepted-but-ignored) — and the
        # contract test requires request_fields to be a subset of the backend
        # schema's properties. Remove this entry together with the backend
        # field in v0.19.0.
        request_fields=frozenset({"channel_ids", "epg_source_ids", "source_order"}),  # EPGMatchRequest
    ),
    "epg_audit_duplicates": Endpoint(
        name="epg_audit_duplicates",
        method="GET",
        path="/api/epg/audit-duplicates",
    ),
    "epg_link_channel": Endpoint(
        name="epg_link_channel",
        method="POST",
        path="/api/epg/channels/{channel_id}/link",
        request_fields=frozenset({"epg_data_id", "tvg_id"}),  # EPGLinkRequest
        # Returns the updated channel dict (linked state) — not a list.
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
    # -- enhancedchannelmanager-omxy5: dummy-EPG profile CRUD --------------
    "dummy_epg_get_profile": Endpoint(
        name="dummy_epg_get_profile",
        method="GET",
        path="/api/dummy-epg/profiles/{profile_id}",
        response_fields=frozenset({"id", "name", "enabled", "channel_group_ids"}),
    ),
    "dummy_epg_create_profile": Endpoint(
        name="dummy_epg_create_profile",
        method="POST",
        path="/api/dummy-epg/profiles",
        request_fields=_DUMMY_EPG_PROFILE_FIELDS,
    ),
    "dummy_epg_update_profile": Endpoint(
        name="dummy_epg_update_profile",
        method="PATCH",
        path="/api/dummy-epg/profiles/{profile_id}",
        request_fields=_DUMMY_EPG_PROFILE_FIELDS,
    ),
    "dummy_epg_delete_profile": Endpoint(
        name="dummy_epg_delete_profile",
        method="DELETE",
        path="/api/dummy-epg/profiles/{profile_id}",
    ),
    "dummy_epg_preview": Endpoint(
        name="dummy_epg_preview",
        method="POST",
        path="/api/dummy-epg/preview",
        request_fields=frozenset({
            "sample_name", "substitution_pairs", "title_pattern", "time_pattern",
            "date_pattern", "title_template", "description_template",
            "upcoming_title_template", "upcoming_description_template",
            "ended_title_template", "ended_description_template",
            "fallback_title_template", "fallback_description_template",
            "event_timezone", "output_timezone", "program_duration",
            "channel_logo_url_template", "program_poster_url_template",
            "pattern_variants", "include_trace",
        }),
        response_fields=frozenset({
            "original_name", "substituted_name", "matched", "matched_variant",
            "rendered", "traces",
        }),
    ),
    # -- cloud-targets domain ----------------------------------------------
    # The Export tab's profile / generate / publish endpoints were removed with
    # the tab (beads vrrxv / 1w428). The cloud-targets list remains, used by the
    # list_cloud_targets MCP tool — cloud targets are still load-bearing for
    # DBAS backup uploads. Relocated from /api/export/cloud-targets to the
    # dedicated /api/cloud-targets router with the Export tab's removal.
    "cloud_list_targets": Endpoint(
        name="cloud_list_targets",
        method="GET",
        path="/api/cloud-targets",
    ),
    # -- enhancedchannelmanager-jcj0f: cloud-target CRUD + test -------------
    # Credentials are Fernet-encrypted at rest; every response masks them
    # (last-4 only) — see routers/cloud_targets.py _mask_credentials. The MCP
    # tools never decrypt or otherwise echo a full credential value.
    "cloud_create_target": Endpoint(
        name="cloud_create_target",
        method="POST",
        path="/api/cloud-targets",
        request_fields=frozenset({"name", "provider_type", "credentials", "upload_path", "enabled"}),
    ),
    "cloud_update_target": Endpoint(
        name="cloud_update_target",
        method="PATCH",
        path="/api/cloud-targets/{target_id}",
        request_fields=frozenset({"name", "provider_type", "credentials", "upload_path", "enabled"}),
    ),
    "cloud_delete_target": Endpoint(
        name="cloud_delete_target",
        method="DELETE",
        path="/api/cloud-targets/{target_id}",
    ),
    "cloud_test_target": Endpoint(
        name="cloud_test_target",
        method="POST",
        path="/api/cloud-targets/{target_id}/test",
        response_fields=frozenset({"success", "message", "provider_info"}),
    ),
    "cloud_test_target_inline": Endpoint(
        name="cloud_test_target_inline",
        method="POST",
        path="/api/cloud-targets/test",
        request_fields=frozenset({"provider_type", "credentials"}),  # CloudTargetTestRequest
        response_fields=frozenset({"success", "message", "provider_info"}),
    ),
    # -- enhancedchannelmanager-jcj0f: sync-target CRUD (no /test endpoint) -
    # backend/routers/sync_targets.py — mirrors cloud-targets CRUD exactly;
    # credentials are Fernet-encrypted at rest, masked in every response.
    "sync_list_targets": Endpoint(
        name="sync_list_targets",
        method="GET",
        path="/api/sync-targets",
    ),
    "sync_get_target": Endpoint(
        name="sync_get_target",
        method="GET",
        path="/api/sync-targets/{target_id}",
    ),
    "sync_create_target": Endpoint(
        name="sync_create_target",
        method="POST",
        path="/api/sync-targets",
        request_fields=frozenset({
            "name", "base_url", "credentials", "enabled", "insecure", "fuzzy_stream_matching",
            "sync_logos",
        }),
    ),
    "sync_update_target": Endpoint(
        name="sync_update_target",
        method="PUT",
        path="/api/sync-targets/{target_id}",
        request_fields=frozenset({
            "name", "base_url", "credentials", "enabled", "insecure", "fuzzy_stream_matching",
            "sync_logos",
        }),
    ),
    "sync_delete_target": Endpoint(
        name="sync_delete_target",
        method="DELETE",
        path="/api/sync-targets/{target_id}",
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
        # enhancedchannelmanager-yd6qh: expanded to the full field set Dispatcharr's
        # M3UAccount accepts (frontend/src/types/index.ts M3UAccountCreateRequest is
        # the authoritative source since the backend body has no Pydantic model).
        # ``server_type``/``url`` are kept for backward compatibility with existing
        # callers (server_type is a legacy alias the tool maps onto account_type);
        # ``account_type`` ("STD"/"XC") is the real Dispatcharr field.
        request_fields=frozenset({
            "name", "url", "server_type", "server_url", "account_type",
            "file_path", "server_group", "max_streams", "is_active",
            "refresh_interval", "username", "password", "stale_stream_days",
            "enable_vod", "auto_enable_new_groups_live",
            "auto_enable_new_groups_vod", "auto_enable_new_groups_series",
        }),
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
    # bd-wwovg / fltt3 gap 3: M3U digest email settings (change-tracking
    # notifications) — backend/routers/m3u_digest.py get/update_m3u_digest_settings.
    # request_fields mirrors M3UDigestSettingsUpdate (m3u_digest.py:27) verbatim;
    # account_ids (bd-wwovg) scopes which M3U accounts' changes are notified.
    "m3u_digest_get_settings": Endpoint(
        name="m3u_digest_get_settings",
        method="GET",
        path="/api/m3u/digest/settings",
    ),
    "m3u_digest_update_settings": Endpoint(
        name="m3u_digest_update_settings",
        method="PUT",
        path="/api/m3u/digest/settings",
        request_fields=frozenset({
            "enabled", "frequency", "email_recipients",
            "include_group_changes", "include_stream_changes",
            "show_detailed_list", "min_changes_threshold", "send_to_discord",
            "exclude_group_patterns", "exclude_stream_patterns", "account_ids",
        }),
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
    # GET /api/normalization/rules — returns {"groups": [{group fields...,
    # "rules": [{rule fields...}]}]}.  Used by list_normalization_rules so
    # rule counts and names are available (groups-only endpoint has no rules).
    "normalization_list_rules": Endpoint(
        name="normalization_list_rules",
        method="GET",
        path="/api/normalization/rules",
    ),
    # PATCH /api/normalization/groups/{group_id} — UpdateRuleGroupRequest
    # (backend/routers/normalization.py:249 update_normalization_group).
    # Wraps the existing backend endpoint; the MCP tool uses only ``enabled``
    # to toggle the global enabled flag on a NormalizationRuleGroup, but the
    # full UpdateRuleGroupRequest field set is declared here so request_fields
    # subset check matches the Pydantic model (bd-svixy).
    "normalization_update_group": Endpoint(
        name="normalization_update_group",
        method="PATCH",
        path="/api/normalization/groups/{group_id}",
        request_fields=frozenset({"name", "description", "enabled", "priority"}),
    ),
    # -- enhancedchannelmanager-e5n8j: normalization CRUD tools --------------
    "normalization_create_group": Endpoint(
        name="normalization_create_group",
        method="POST",
        path="/api/normalization/groups",
        request_fields=frozenset({"name", "description", "enabled", "priority"}),  # CreateRuleGroupRequest
    ),
    "normalization_get_group": Endpoint(
        name="normalization_get_group",
        method="GET",
        path="/api/normalization/groups/{group_id}",
        # Response includes group fields + a nested "rules" list — used by the
        # delete_normalization_group preview to show the cascade blast radius.
        response_fields=frozenset({"id", "name", "rules"}),
    ),
    "normalization_delete_group": Endpoint(
        name="normalization_delete_group",
        method="DELETE",
        path="/api/normalization/groups/{group_id}",
        response_fields=frozenset({"status", "id", "rules_cleaned"}),
    ),
    "normalization_get_rule": Endpoint(
        name="normalization_get_rule",
        method="GET",
        path="/api/normalization/rules/{rule_id}",
        response_fields=frozenset({"id", "name"}),
    ),
    "normalization_create_rule": Endpoint(
        name="normalization_create_rule",
        method="POST",
        path="/api/normalization/rules",
        # CreateRuleRequest — full field set.
        request_fields=frozenset({
            "group_id", "name", "description", "enabled", "priority",
            "condition_type", "condition_value", "case_sensitive",
            "tag_group_id", "tag_match_position", "require_delimiter",
            "conditions", "condition_logic",
            "action_type", "action_value",
            "else_action_type", "else_action_value", "stop_processing",
        }),
    ),
    "normalization_update_rule": Endpoint(
        name="normalization_update_rule",
        method="PATCH",
        path="/api/normalization/rules/{rule_id}",
        # UpdateRuleRequest — same field names as create, minus group_id
        # (a rule's group is fixed at creation; PATCH doesn't move it).
        request_fields=frozenset({
            "name", "description", "enabled", "priority",
            "condition_type", "condition_value", "case_sensitive",
            "tag_group_id", "tag_match_position", "require_delimiter",
            "conditions", "condition_logic",
            "action_type", "action_value",
            "else_action_type", "else_action_value", "stop_processing",
        }),
    ),
    "normalization_delete_rule": Endpoint(
        name="normalization_delete_rule",
        method="DELETE",
        path="/api/normalization/rules/{rule_id}",
    ),
    "normalization_apply_to_channels": Endpoint(
        name="normalization_apply_to_channels",
        method="POST",
        path="/api/normalization/apply-to-channels",
        # ``dry_run`` is a QUERY param (FastAPI ``Query``, default True); the
        # per-channel execute-mode decisions live in the body's ``actions``.
        query_params=frozenset({"dry_run"}),
        request_fields=frozenset({"actions"}),  # ApplyToChannelsRequest
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
        # Backend DELETE /api/notifications accepts read_only (bool, default True)
        # to control whether unread notifications are included (bd-1wq7z.14).
        query_params=frozenset({"read_only"}),
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
    # -- co5wh.2: per-provider stats (Stats v2 Providers panel, bd-skqln.16) --
    # All four routes are admin-only on the backend; the MCP static key is
    # admin-equivalent (bd-1wq7z.1). None declare a response_model so
    # response_is_list stays at its default (False) — the contract test
    # compares against the OpenAPI schema which has no array type declared.
    "stats_providers_buffering": Endpoint(
        name="stats_providers_buffering",
        method="GET",
        path="/api/stats/providers/buffering",
        query_params=frozenset({"window", "bucket"}),
    ),
    "stats_providers_watch_time": Endpoint(
        name="stats_providers_watch_time",
        method="GET",
        path="/api/stats/providers/watch-time",
        query_params=frozenset({"window"}),
    ),
    "stats_providers_channel_heatmap": Endpoint(
        name="stats_providers_channel_heatmap",
        method="GET",
        path="/api/stats/providers/channel-heatmap",
        query_params=frozenset({"window", "top_n"}),
    ),
    "stats_providers_bitrate": Endpoint(
        name="stats_providers_bitrate",
        method="GET",
        path="/api/stats/providers/bitrate",
        query_params=frozenset({"window", "bucket"}),
    ),
    # bd-n5cwp / fltt3 gap 2: per-provider stream-assignment usage (config
    # data, not viewing telemetry — not admin-gated, unlike the four
    # providers/* endpoints above). See backend/routers/stats.py
    # get_provider_stream_usage.
    "stats_providers_stream_usage": Endpoint(
        name="stats_providers_stream_usage",
        method="GET",
        path="/api/stats/providers/stream-usage",
        query_params=frozenset({"bypass_cache"}),
    ),
    # -- co5wh.3: per-user watch-time (Stats v2 GH-62, bd-skqln.5) -----------
    # /watch-time: group_by=total|day; response_is_list stays default (untyped).
    # /users/dispatcharr/{user_id} and /users/emby/{emby_user_id}: path-param
    # routes; no body; no typed response_model in OpenAPI.
    "stats_watch_time": Endpoint(
        name="stats_watch_time",
        method="GET",
        path="/api/stats/watch-time",
        query_params=frozenset({"group_by", "user_id", "from", "to"}),
    ),
    "stats_users_dispatcharr": Endpoint(
        name="stats_users_dispatcharr",
        method="GET",
        path="/api/stats/users/dispatcharr/{user_id}",
        query_params=frozenset({"from", "to"}),
    ),
    "stats_users_emby": Endpoint(
        name="stats_users_emby",
        method="GET",
        path="/api/stats/users/emby/{emby_user_id}",
        query_params=frozenset({"from", "to"}),
    ),
    # -- co5wh.4: popularity trending + per-channel score --------------------
    # /popularity/trending: direction + limit query params; returns a runtime
    # list of ChannelPopularityScore.to_dict() — no response_model in OpenAPI.
    # /popularity/channel/{channel_id}: path-param; returns one score dict or
    # 404; no response_model in OpenAPI.
    # response_is_list stays default (False) — untyped in OpenAPI.
    "stats_popularity_trending": Endpoint(
        name="stats_popularity_trending",
        method="GET",
        path="/api/stats/popularity/trending",
        query_params=frozenset({"direction", "limit"}),
    ),
    "stats_popularity_channel": Endpoint(
        name="stats_popularity_channel",
        method="GET",
        path="/api/stats/popularity/channel/{channel_id}",
    ),
    # -- co5wh.5: activity feed + per-channel bandwidth ----------------------
    # /activity: proxies Dispatcharr /api/core/system-events/; returns a
    # Dispatcharr paginated dict {count, next, previous, results:[...]} — no
    # response_model in OpenAPI. response_is_list stays default (False).
    # /channel-bandwidth: BandwidthTracker.get_channel_bandwidth_stats returns
    # a plain list — again no response_model declared in OpenAPI.
    "stats_activity": Endpoint(
        name="stats_activity",
        method="GET",
        path="/api/stats/activity",
        query_params=frozenset({"limit", "offset", "event_type"}),
    ),
    "stats_channel_bandwidth": Endpoint(
        name="stats_channel_bandwidth",
        method="GET",
        path="/api/stats/channel-bandwidth",
        query_params=frozenset({"days", "limit", "sort_by"}),
    ),
    # -- streams domain ----------------------------------------------------
    "streams_list": Endpoint(
        name="streams_list",
        method="GET",
        path="/api/streams",
        query_params=frozenset(
            {"page", "page_size", "search", "channel_group_name", "m3u_account",
             "sort", "enrich", "include_assignment", "bypass_cache"}
        ),
    ),
    "streams_by_ids": Endpoint(
        name="streams_by_ids",
        method="POST",
        path="/api/streams/by-ids",
        request_fields=frozenset({"stream_ids", "include_assignment"}),  # BulkStreamIdsRequest
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
    "stream_stats_stale": Endpoint(
        name="stream_stats_stale",
        method="GET",
        path="/api/stream-stats/stale",
        query_params=frozenset({"days"}),
    ),
    # enhancedchannelmanager-po78p / fltt3 gap 1: cheap stale-id set (Dispatcharr
    # is_stale flag only) — distinct from stream_stats_stale above, which is the
    # heavier ffprobe-age + provider-stale report. See backend/routers/streams.py
    # get_stale_stream_ids.
    "streams_stale_ids": Endpoint(
        name="streams_stale_ids",
        method="GET",
        path="/api/streams/stale-ids",
        query_params=frozenset({"bypass_cache"}),
        response_fields=frozenset({"stale_stream_ids", "last_seen", "count"}),
    ),
    # -- enhancedchannelmanager-rv5w1: probe lifecycle + circuit breaker ----
    "stream_stats_probe_history": Endpoint(
        name="stream_stats_probe_history",
        method="GET",
        path="/api/stream-stats/probe/history",
    ),
    "stream_stats_probe_reset": Endpoint(
        name="stream_stats_probe_reset",
        method="POST",
        path="/api/stream-stats/probe/reset",
    ),
    "stream_stats_dismiss": Endpoint(
        name="stream_stats_dismiss",
        method="POST",
        path="/api/stream-stats/dismiss",
        request_fields=frozenset({"stream_ids"}),  # DismissStatsRequest
    ),
    "stream_stats_dismissed": Endpoint(
        name="stream_stats_dismissed",
        method="GET",
        path="/api/stream-stats/dismissed",
    ),
    "channel_pipeline_circuit_breaker": Endpoint(
        name="channel_pipeline_circuit_breaker",
        method="GET",
        path="/api/channel-pipeline/circuit-breaker",
    ),
    "channel_pipeline_reset_circuit_breaker": Endpoint(
        name="channel_pipeline_reset_circuit_breaker",
        method="POST",
        path="/api/channel-pipeline/reset-circuit-breaker",
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
    # bd-0hjrk.5 — POST /api/backup/save PERSISTS the full backup ZIP to
    # BACKUPS_DIR (unlike GET /create which only streams). No request body
    # (admin-guarded; the backend builds the artifact). Returns
    # {filename, size_bytes, created_at} — a bare dict (no response_model), so
    # response_fields stays empty (the tool reads keys off the runtime dict).
    "backup_save": Endpoint(
        name="backup_save",
        method="POST",
        path="/api/backup/save",
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
    # bd-0hjrk.5 — POST /api/backup/restore-saved restores from an on-disk saved
    # ZIP (RestoreSavedRequest body = {"filename": ...}). The backend validates
    # the filename via the strict regex + containment guard and reuses the same
    # restore code path as the uploaded-ZIP POST /restore. Admin-guarded.
    "backup_restore_saved": Endpoint(
        name="backup_restore_saved",
        method="POST",
        path="/api/backup/restore-saved",
        request_fields=frozenset({"filename"}),  # RestoreSavedRequest
    ),
    # bd-0wmeg — POST /api/backup/restore-dbas-saved restores a SAVED on-disk
    # DBAS artifact by filename (RestoreDbasSavedRequest body = {filename,
    # confirm_apply, passphrase}). The saved-file analogue of the upload-based
    # POST /restore-dbas; handles the v0.18.0 DBAS format incl. encrypted
    # artifacts (passphrase). Backend validates the filename via the strict
    # regex + containment guard and kicks the async dbas_restore task with
    # cleanup_artifact=False (the operator's saved file survives). Admin-guarded.
    # SECURITY: ``passphrase`` is forwarded to the task but never logged/echoed.
    "backup_restore_dbas_saved": Endpoint(
        name="backup_restore_dbas_saved",
        method="POST",
        path="/api/backup/restore-dbas-saved",
        request_fields=frozenset({"filename", "confirm_apply", "passphrase"}),
    ),
    "journal_list": Endpoint(
        name="journal_list",
        method="GET",
        path="/api/journal",
        query_params=frozenset(
            {"page", "page_size", "category", "action_type", "date_from", "date_to", "search", "user_initiated", "batch_id"}
        ),
    ),
    # -- channel_merges (dedup) domain — BD-O (bd-70ylc) -----------------
    # ADR-008 §D7: tool names are the contract; these endpoints mirror the
    # REST surface in backend/routers/channel_merges.py.
    "channel_merges_list": Endpoint(
        name="channel_merges_list",
        method="GET",
        path="/api/channel-merges",
        query_params=frozenset({"status", "group_id", "page", "page_size"}),
        response_fields=frozenset({"merges", "total", "page", "page_size", "total_pages"}),
    ),
    "channel_merges_accept": Endpoint(
        name="channel_merges_accept",
        method="POST",
        path="/api/channel-merges/{merge_id}/accept",
        response_fields=frozenset(
            {
                "merged_into_channel_id", "journal_entry_id", "source_stream_id",
                "confidence", "status",
                # Whether DISPATCHARR was actually updated, and why not when it
                # was not. `status` describes the QUEUE ROW; an accept whose
                # stream-name match was zero or ambiguous used to return
                # `'merged'` with nothing anywhere saying the upstream write
                # never happened (bead enhancedchannelmanager-i5ic0). `status`
                # is now `'merged'` or `'pending'` — a merge ECM could not
                # apply stays queued, flagged, and retryable (PO decision
                # 2026-08-16).
                "dispatcharr_updated", "unapplied_reason",
                "journal_rows_unwritten",
            }
        ),
    ),
    "channel_merges_dismiss": Endpoint(
        name="channel_merges_dismiss",
        method="POST",
        path="/api/channel-merges/{merge_id}/dismiss",
        response_fields=frozenset({"journal_entry_id", "status"}),
    ),
    # -- channel_merges candidates — BD-P (bd-7u8ms) consumer ---------------
    # ADR-008 §D7. The list/accept/dismiss endpoints above are owned by BD-O
    # (bd-70ylc); BD-P only owns candidates since add_stream's dedup_action
    # is the sole consumer.
    "channel_merges_candidates": Endpoint(
        name="channel_merges_candidates",
        method="GET",
        path="/api/channel-merges/candidates",
        query_params=frozenset({"stream_name", "group_id", "page", "page_size"}),
        response_fields=frozenset({"stream_name", "candidates", "total", "page", "page_size", "total_pages"}),
    ),
    # -- channel_merges enqueue — bd-b3czq (ADR-008 §D7 MCP prompt path) -----
    # POST /api/channel-merges async-queues a merge candidate (creates a
    # pending_merges row with trigger_context='mcp_tool') and returns a
    # merge_id so add_stream(dedup_action='prompt') can hand the agent a row
    # to accept/dismiss. The server re-runs the matcher; the tool sends only
    # the stream context (NOT a confidence — the action-time score is
    # authoritative per §D6).
    "channel_merges_enqueue": Endpoint(
        name="channel_merges_enqueue",
        method="POST",
        path="/api/channel-merges",
        request_fields=frozenset({"stream_name", "group_id"}),
        response_fields=frozenset(
            {
                "merge_id",
                "created",
                "candidate_channel_id",
                "candidate_channel_name",
                "confidence",
                "meets_threshold",
                "status",
            }
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
    # -- tags domain — enhancedchannelmanager-dswrl -------------------------
    # backend/routers/tags.py. Used by normalization conditions
    # (tag_group_id/tag_match_position) and channel-pipeline rule matching.
    "tags_list_groups": Endpoint(
        name="tags_list_groups",
        method="GET",
        path="/api/tags/groups",
        response_fields=frozenset({"groups"}),
    ),
    "tags_create_group": Endpoint(
        name="tags_create_group",
        method="POST",
        path="/api/tags/groups",
        request_fields=frozenset({"name", "description"}),  # CreateTagGroupRequest
    ),
    "tags_get_group": Endpoint(
        name="tags_get_group",
        method="GET",
        path="/api/tags/groups/{group_id}",
        # to_dict(include_tags=True) — used by delete_tag_group's cascade preview.
        response_fields=frozenset({"id", "name", "is_builtin", "tags"}),
    ),
    "tags_update_group": Endpoint(
        name="tags_update_group",
        method="PATCH",
        path="/api/tags/groups/{group_id}",
        request_fields=frozenset({"name", "description"}),  # UpdateTagGroupRequest
    ),
    "tags_delete_group": Endpoint(
        name="tags_delete_group",
        method="DELETE",
        path="/api/tags/groups/{group_id}",
    ),
    "tags_add_to_group": Endpoint(
        name="tags_add_to_group",
        method="POST",
        path="/api/tags/groups/{group_id}/tags",
        request_fields=frozenset({"tags", "case_sensitive"}),  # CreateTagsRequest
        response_fields=frozenset({"created", "skipped", "group_id"}),
    ),
    "tags_update_tag": Endpoint(
        name="tags_update_tag",
        method="PATCH",
        path="/api/tags/groups/{group_id}/tags/{tag_id}",
        request_fields=frozenset({"enabled", "case_sensitive"}),  # UpdateTagRequest
    ),
    "tags_delete_tag": Endpoint(
        name="tags_delete_tag",
        method="DELETE",
        path="/api/tags/groups/{group_id}/tags/{tag_id}",
    ),
    "tags_test": Endpoint(
        name="tags_test",
        method="POST",
        path="/api/tags/test",
        request_fields=frozenset({"text", "group_id"}),  # TestTagsRequest
        response_fields=frozenset({"text", "group_id", "group_name", "matches", "match_count"}),
    ),
    # -- emby domain --------------------------------------------------------
    # POST returns 202 {job_id, status}; the status-poll GET
    # /api/emby/clear-logos/{job_id} is a raw contract-exempt client.get
    # (mirrors the bulk-commit 202+poll shape, bd-ggxks / GH #475).
    "emby_clear_logos": Endpoint(
        name="emby_clear_logos",
        method="POST",
        path="/api/emby/clear-logos",
        request_fields=frozenset({"logo_types", "channel_ids"}),
    ),
    # -- event_sync team-alias dictionary (bead ti939.4.2) ------------------
    # Deliberately placed at the very END of the registry, away from the
    # exclusion-domain (~line 405) and digest/stats insertion points of the
    # in-flight PRs #705/#706, to avoid textual merge overlap.
    # Operator known-equivalence groups ("Man Utd" == "Manchester United"
    # == "MUFC") consulted by the event matcher's team-token layer on BOTH
    # the hard-reject and boost paths. Full-replace PUT; backend validates
    # against the matcher's own term normalization and journals the change.
    "es_get_team_aliases": Endpoint(
        name="es_get_team_aliases",
        method="GET",
        path="/api/event-sync/team-aliases",
        response_fields=frozenset({"groups"}),
    ),
    "es_update_team_aliases": Endpoint(
        name="es_update_team_aliases",
        method="PUT",
        path="/api/event-sync/team-aliases",
        request_fields=frozenset({"groups"}),  # TeamAliasesUpdateRequest
        response_fields=frozenset({"groups"}),
    ),
}
