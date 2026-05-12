"""MCP-tool ↔ backend-API contract test (bd-vtghg, full surface as of Phase 2).

Cross-checks the declarative endpoint registry the MCP tools call through
(``mcp-server/_endpoint_contracts.py :: ENDPOINTS``) against the backend's
*live* OpenAPI spec (``app.openapi()``). The registry is the single source of
truth that both the MCP tools and this test consume — so a tool that drifts
from the registry fails the call-time guard in ``ECMClient.call_endpoint``, and
a registry entry that drifts from the backend fails *here*.

What this catches (the recent GH #221-225 drift class):
  * GH #221 — ``group_id`` vs ``channel_group_id``: ``request_fields`` must be a
    subset of the request-body schema's properties.
  * GH #222 — the ``{"rules": [...]}`` envelope vs a bare list: ``response_fields``
    / ``response_is_list`` checked against the 2xx response schema.
  * a tool calling a path/method that doesn't exist on the backend.

It also scans *all* MCP source (``mcp-server/tools/*.py`` +
``mcp-server/resources/*.py``) for any ``client.<verb>("/api/...")`` literal
that hasn't been migrated to ``call_endpoint`` and lacks a
``# contract-exempt:`` comment — that's a FAIL (Phase 2 flipped this from
WARN to FAIL across the whole surface).

The ``_endpoint_contracts`` module is pure stdlib + ``dataclasses``, so it
imports fine in the backend venv.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

# Make mcp-server/ importable for _endpoint_contracts.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_MCP_DIR = _REPO_ROOT / "mcp-server"
if str(_MCP_DIR) not in sys.path:
    sys.path.insert(0, str(_MCP_DIR))

from _endpoint_contracts import ENDPOINTS, Endpoint  # noqa: E402


# ---------------------------------------------------------------------------
# OpenAPI helpers
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def openapi_spec():
    from main import app
    return app.openapi()


def _resolve_ref(spec: dict, schema: dict) -> dict:
    """Follow a single ``$ref`` into ``components/schemas`` (one level)."""
    ref = schema.get("$ref")
    if not ref:
        return schema
    # ref looks like "#/components/schemas/Foo"
    parts = ref.lstrip("#/").split("/")
    node: dict = spec
    for p in parts:
        node = node[p]
    return node


def _schema_property_names(spec: dict, schema: dict, _seen: set[str] | None = None) -> set[str]:
    """Collect declared property names from a schema, chasing ``$ref`` and
    unioning the sub-schemas of ``allOf`` / ``anyOf`` / ``oneOf``.

    A schema that has no ``properties`` (e.g. a free-form ``{"type": "object"}``
    body from a FastAPI ``data: dict`` param, or an empty response schema from a
    route with no ``response_model``) yields the empty set — callers treat that
    as "can't cross-check" rather than "no fields allowed".
    """
    if schema is None:
        return set()
    _seen = _seen if _seen is not None else set()
    ref = schema.get("$ref")
    if ref:
        if ref in _seen:
            return set()
        _seen.add(ref)
        schema = _resolve_ref(spec, schema)
    names: set[str] = set()
    props = schema.get("properties")
    if isinstance(props, dict):
        names |= set(props.keys())
    for combiner in ("allOf", "anyOf", "oneOf"):
        for sub in schema.get(combiner, []) or []:
            names |= _schema_property_names(spec, sub, _seen)
    return names


def _is_free_object_schema(spec: dict, schema: dict, _seen: set[str] | None = None) -> bool:
    """True if the schema describes an open-ended object with no declared
    properties (``{"type": "object"}`` / ``additionalProperties``) — i.e. the
    backend accepts an arbitrary dict, so ``request_fields`` can't be
    cross-checked (the call-time guard still applies).
    """
    if schema is None:
        return False
    _seen = _seen if _seen is not None else set()
    ref = schema.get("$ref")
    if ref:
        if ref in _seen:
            return False
        _seen.add(ref)
        schema = _resolve_ref(spec, schema)
    if _schema_property_names(spec, schema):
        return False
    if schema.get("type") == "object":
        return True
    if "additionalProperties" in schema:
        return True
    # An empty `{}` schema (no type, no properties) — also "open".
    if not schema:
        return True
    return False


def _schema_is_array(spec: dict, schema: dict, _seen: set[str] | None = None) -> bool:
    if schema is None:
        return False
    _seen = _seen if _seen is not None else set()
    ref = schema.get("$ref")
    if ref:
        if ref in _seen:
            return False
        _seen.add(ref)
        schema = _resolve_ref(spec, schema)
    if schema.get("type") == "array":
        return True
    for combiner in ("allOf", "anyOf", "oneOf"):
        for sub in schema.get(combiner, []) or []:
            if _schema_is_array(spec, sub, _seen):
                return True
    return False


def _path_item(spec: dict, ep: Endpoint) -> dict:
    """Return the ``spec["paths"][path][method]`` operation object, asserting
    the (method, path) pair exists. FastAPI uses the same ``{name}`` style as
    our Endpoint.path, so no normalisation is needed.
    """
    paths = spec.get("paths", {})
    assert ep.path in paths, (
        f"Endpoint {ep.name!r}: path {ep.path!r} not in the backend OpenAPI "
        f"spec. The tool calls a path that doesn't exist (or the path string "
        f"in mcp-server/_endpoint_contracts.py is wrong)."
    )
    method = ep.method.lower()
    item = paths[ep.path]
    assert method in item, (
        f"Endpoint {ep.name!r}: {ep.method} not declared for {ep.path!r} "
        f"(declared methods: {sorted(k for k in item if k in {'get','post','put','patch','delete'})})."
    )
    return item[method]


def _request_body_schema(spec: dict, operation: dict) -> dict | None:
    rb = operation.get("requestBody")
    if not rb:
        return None
    content = rb.get("content", {})
    media = content.get("application/json")
    if not media:
        # multipart / other — not a JSON-body endpoint; skip cross-check.
        return None
    return media.get("schema")


def _success_response_schema(spec: dict, operation: dict) -> dict | None:
    responses = operation.get("responses", {})
    for code in ("200", "201", "202"):
        if code in responses:
            content = responses[code].get("content", {})
            media = content.get("application/json")
            if media:
                return media.get("schema")
            return None  # 2xx with no JSON body
    return None


def _query_param_names(operation: dict) -> set[str]:
    return {
        p["name"]
        for p in operation.get("parameters", [])
        if p.get("in") == "query"
    }


# ---------------------------------------------------------------------------
# Per-endpoint contract checks
# ---------------------------------------------------------------------------

_MODELLED_ENDPOINTS = [ep for ep in ENDPOINTS.values() if ep.exempt_reason is None]


@pytest.mark.parametrize("ep", _MODELLED_ENDPOINTS, ids=[ep.name for ep in _MODELLED_ENDPOINTS])
def test_endpoint_matches_backend_openapi(openapi_spec, ep: Endpoint):
    operation = _path_item(openapi_spec, ep)

    # --- request body fields (the GH #221 catcher) ---
    if ep.request_fields:
        body_schema = _request_body_schema(openapi_spec, operation)
        if body_schema is None or _is_free_object_schema(openapi_spec, body_schema):
            # Either the backend declares its body via a raw ``request: Request``
            # param (no OpenAPI body schema at all) or via a free-form ``dict``
            # (e.g. FastAPI `data: dict`) — both accept arbitrary keys, so there's
            # nothing to cross-check here. The call-time subset guard in
            # call_endpoint still constrains the tools to ep.request_fields.
            pass
        else:
            declared = _schema_property_names(openapi_spec, body_schema)
            missing = ep.request_fields - declared
            assert not missing, (
                f"Endpoint {ep.name!r}: request_fields {sorted(missing)} are NOT "
                f"accepted by the backend body for {ep.method} {ep.path}. "
                f"Backend accepts: {sorted(declared)}. "
                f"(This is the GH #221 'group_id vs channel_group_id' drift class — "
                f"fix the tool/registry to match the backend Pydantic model.)"
            )

    # --- query params ---
    if ep.query_params:
        declared_q = _query_param_names(operation)
        missing_q = ep.query_params - declared_q
        assert not missing_q, (
            f"Endpoint {ep.name!r}: query_params {sorted(missing_q)} are NOT "
            f"declared on {ep.method} {ep.path}. Backend declares: {sorted(declared_q)}."
        )

    # --- response shape (the GH #222 catcher) ---
    resp_schema = _success_response_schema(openapi_spec, operation)
    if resp_schema is not None:
        is_array = _schema_is_array(openapi_spec, resp_schema)
        assert is_array == ep.response_is_list, (
            f"Endpoint {ep.name!r}: response_is_list={ep.response_is_list} but the "
            f"backend 2xx response for {ep.method} {ep.path} is "
            f"{'an array' if is_array else 'not an array'}. "
            f"(The GH #222 '{{\"rules\": [...]}} envelope vs bare list' drift class.)"
        )
        if ep.response_fields and not is_array:
            declared_r = _schema_property_names(openapi_spec, resp_schema)
            if declared_r:  # only cross-check when the route declares a model
                missing_r = ep.response_fields - declared_r
                assert not missing_r, (
                    f"Endpoint {ep.name!r}: response_fields {sorted(missing_r)} are "
                    f"NOT in the backend 2xx response for {ep.method} {ep.path}. "
                    f"Backend returns: {sorted(declared_r)}."
                )
    elif ep.response_is_list:
        pytest.fail(
            f"Endpoint {ep.name!r}: response_is_list=True but {ep.method} {ep.path} "
            f"has no JSON response body in the OpenAPI spec."
        )


# ---------------------------------------------------------------------------
# Tool-source guard
# ---------------------------------------------------------------------------

_TOOLS_DIR = _MCP_DIR / "tools"
_RESOURCES_DIR = _MCP_DIR / "resources"


def _source_files() -> list[Path]:
    """All MCP source files the guard scans: tools/*.py + resources/*.py."""
    files = sorted(_TOOLS_DIR.glob("*.py")) + sorted(_RESOURCES_DIR.glob("*.py"))
    return [f for f in files if f.name != "__init__.py"]


# `client.post("/api/...")`, `client.get("/api/...")`, etc. — the path arg may
# be a literal "/api/..." or an f-string f"/api/...".
_RAW_CALL_RE = re.compile(
    r'client\.(get|post|patch|put|delete)\(\s*f?["\'](/api/[^"\']*)["\']'
)
_CALL_ENDPOINT_RE = re.compile(r'call_endpoint\(\s*ENDPOINTS\[\s*["\']([^"\']+)["\']\s*\]')


def _scan_tool_file(path: Path):
    """Return (raw_calls, endpoint_refs) for an MCP source file.

    ``raw_calls`` is a list of (lineno, verb, api_path, is_exempt) tuples;
    ``endpoint_refs`` is a list of ENDPOINTS keys referenced via call_endpoint.
    """
    raw_calls = []
    endpoint_refs = []
    for lineno, line in enumerate(path.read_text().splitlines(), start=1):
        for m in _RAW_CALL_RE.finditer(line):
            is_exempt = "# contract-exempt:" in line
            raw_calls.append((lineno, m.group(1), m.group(2), is_exempt))
        for m in _CALL_ENDPOINT_RE.finditer(line):
            endpoint_refs.append(m.group(1))
    return raw_calls, endpoint_refs


def test_no_unmarked_raw_api_calls_in_mcp_sources():
    """FAIL-mode (bd-vtghg Phase 2): every `client.<verb>("/api/...")` literal
    in any ``mcp-server/tools/*.py`` or ``mcp-server/resources/*.py`` must
    either route through ``call_endpoint(ENDPOINTS[...])`` or carry a
    ``# contract-exempt: <reason>`` comment on the call line. After Phase 2
    there should be zero un-exempt raw `/api/...` calls.
    """
    offenders: list[str] = []
    for path in _source_files():
        raw_calls, _ = _scan_tool_file(path)
        for lineno, verb, api_path, is_exempt in raw_calls:
            if not is_exempt:
                offenders.append(f"{path.name}:{lineno}  client.{verb}({api_path!r})")
    assert not offenders, (
        "MCP tool/resource source still has un-migrated raw /api/ calls "
        "without a `# contract-exempt:` marker (route them through "
        "call_endpoint(ENDPOINTS[...]) or add the marker):\n  "
        + "\n  ".join(offenders)
    )


def test_call_endpoint_refs_exist_in_registry():
    """Every `call_endpoint(ENDPOINTS["X"])` reference (any MCP source file)
    must name a key that exists in ENDPOINTS.
    """
    bad: list[str] = []
    for path in _source_files():
        _, endpoint_refs = _scan_tool_file(path)
        for key in endpoint_refs:
            if key not in ENDPOINTS:
                bad.append(f"{path.name}: ENDPOINTS[{key!r}] is not defined")
    assert not bad, "Unknown endpoint ids referenced:\n  " + "\n  ".join(bad)


def test_contract_exempt_inventory(capsys):
    """Visibility (always passes): prints the inventory of the few raw
    `client.<verb>("/api/...")` calls that remain — all of which must be
    `# contract-exempt:` (the FAIL-mode guard above enforces that). Lets a
    reviewer see at a glance which tools intentionally bypass the registry.
    """
    inventory: list[str] = []
    for path in _source_files():
        raw_calls, _ = _scan_tool_file(path)
        for lineno, verb, api_path, is_exempt in raw_calls:
            tag = "" if is_exempt else "  <<< NOT EXEMPT — this is a bug"
            inventory.append(f"{path.name}:{lineno}  {verb.upper()} {api_path}{tag}")
    with capsys.disabled():
        if inventory:
            print(f"\n[bd-vtghg] contract-exempt raw /api/ calls ({len(inventory)}):")
            for entry in inventory:
                print("  " + entry)
        else:
            print("\n[bd-vtghg] no raw /api/ calls remain — every tool routes through the registry.")
    assert True
