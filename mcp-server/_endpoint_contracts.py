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

**Scope.** Phase 1 of ``enhancedchannelmanager-vtghg`` covers the ``channels``
and ``auto_creation`` MCP tool domains only — the ~30 endpoints those tools
hit, plus the contract-enforcement machinery as a proof of concept. Phase 2
migrates the remaining ~11 domains and flips the tool-source guard from WARN to
FAIL on any unmigrated ``client.<verb>("/api/...")`` literal.

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
}
