"""Contract sweep: every Dispatcharr URL template the client issues must exist.

Bead ``enhancedchannelmanager-ax0kf`` / **ADR-014** (`docs/adr/ADR-014-dispatcharr-api-drift-strategy.md`).

ECM's Dispatcharr REST client was written against a *guessed* surface, and the
guesses shipped: ``enhancedchannelmanager-q6xjl`` (a key-string settings URL
that 404s on every restore key) and ``enhancedchannelmanager-lsa0s``
(``/api/dvr/rules/``, a path that exists on no Dispatcharr version and returns
the SPA shell as ``200 text/html``). Both were single-path bugs found by an
operator, not by CI.

This module closes the *existence* class of that bug for the whole client at
once. It walks ``backend/dispatcharr_client.py`` with :mod:`ast`, derives every
``(HTTP method, URL template)`` pair the client can issue, and checks each one
against a recorded manifest of the live Dispatcharr OpenAPI document
(``tests/fixtures/dispatcharr_openapi_paths_manifest.json``, produced by
``scripts/record_dispatcharr_openapi_manifest.py``).

**What it proves:** the path exists upstream, the method is allowed on it, and
the number/type of path parameters lines up.

**What it deliberately does NOT prove:** request or response *shape*, field
names, semantics, or query parameters. That is the job of the deep recorded
fixtures (``dispatcharr_openapi_recorded.json``,
``dispatcharr_core_settings_recorded.json``,
``dispatcharr_dvr_recurring_rules_recorded.json``), which pin a few high-risk
paths literally. A green sweep means "no endpoint is imaginary", not "the
integration works" — see ADR-014 Consequences.

**The extraction is derived from source, never hand-copied**, and any path
expression it cannot resolve is a LOUD test failure rather than a silent skip.
A partial sweep that reports green would recreate, in miniature, the exact
failure this bead exists to fix: a claim of coverage that was never real.

The whole check is hermetic — it reads a recorded fixture and never contacts a
Dispatcharr instance.
"""
from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Sequence

import pytest

import dispatcharr_client

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_CLIENT_SOURCE_PATH = _BACKEND_DIR / "dispatcharr_client.py"
_MANIFEST_PATH = (
    _BACKEND_DIR / "tests" / "fixtures" / "dispatcharr_openapi_paths_manifest.json"
)

_CLIENT_CLASS_NAME = "DispatcharrClient"

# Attribute names on ``self._client`` (the raw httpx.AsyncClient) that can put a
# request on the wire. Every call site using one of these is either extracted as
# a client call (the verb methods) or must appear in the reviewed plumbing set
# below — never silently ignored.
_RAW_HTTP_ATTRS = frozenset(
    {"get", "post", "put", "patch", "delete", "request", "send", "build_request", "stream"}
)
_RAW_VERB_ATTRS = frozenset({"get", "post", "put", "patch", "delete"})

# Raw-httpx call sites that carry no extractable URL template of their own
# because they are the client's own plumbing: ``_request`` forwards a method +
# path it was given, and ``_get_json_bounded`` forwards a path parameter whose
# call sites ARE extracted (see ``_PATH_FORWARDING_HELPERS``). Reviewed once,
# recorded here; anything NEW that reaches httpx directly fails
# ``test_no_unreviewed_raw_http_call_sites`` until a human classifies it.
_REVIEWED_PLUMBING_CALL_SITES = frozenset(
    {
        ("_request", "request"),
        ("_get_json_bounded", "build_request"),
        ("_get_json_bounded", "send"),
    }
)

# Client-internal helpers whose FIRST positional argument is a URL path and
# which issue a known method. Their call sites are extracted exactly like
# ``self._request`` ones, so the paths they forward stay covered.
_PATH_FORWARDING_HELPERS = {"_get_json_bounded": "GET"}

# The one (method, template) pair that is legitimately absent from the manifest:
# drf-spectacular does not document its own schema route inside the document it
# generates. This is an exemption of exactly one entry, and
# ``test_schema_endpoint_exemption_is_still_required`` fails if it ever becomes
# unnecessary — so it cannot rot into an allowlist that hides real drift.
_SELF_DESCRIBING_EXEMPTIONS = frozenset({("GET", "/api/schema/")})

# Annotations that mean "this interpolation is an integer id". Used only to
# catch the drift an integer->UUID primary-key migration would produce.
_INT_ANNOTATIONS = frozenset({"int", "Optional[int]", "int | None", "Union[int, None]"})

# ``format`` values that an integer id still satisfies.
_INT_COMPATIBLE_FORMATS = frozenset({"int32", "int64"})


# ---------------------------------------------------------------------------
# Extraction — derived from the client's own source
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClientCall:
    """One ``(method, URL template)`` the client can issue, and who issues it."""

    method: str
    template: str
    parameter_annotations: tuple[Optional[str], ...]
    owner: str
    lineno: int

    def describe(self) -> str:
        return (
            f"{self.method} {self.template} "
            f"(DispatcharrClient.{self.owner}, {_CLIENT_SOURCE_PATH.name}:{self.lineno})"
        )


@dataclass(frozen=True)
class UnresolvedExpression:
    """A path/method expression the extractor could not statically resolve."""

    owner: str
    lineno: int
    expression: str
    reason: str

    def describe(self) -> str:
        return (
            f"DispatcharrClient.{self.owner} line {self.lineno}: {self.reason} "
            f"-- {self.expression}"
        )


@dataclass(frozen=True)
class RawCallSite:
    """A direct ``self._client.<attr>(...)`` call site."""

    owner: str
    attribute: str
    lineno: int


@dataclass
class ExtractionResult:
    calls: list[ClientCall] = field(default_factory=list)
    unresolved: list[UnresolvedExpression] = field(default_factory=list)
    raw_call_sites: list[RawCallSite] = field(default_factory=list)

    @property
    def templates(self) -> set[tuple[str, str]]:
        return {(call.method, call.template) for call in self.calls}


class _Unresolvable(Exception):
    """Internal signal: a path expression is not statically resolvable."""


def client_module_constants() -> dict[str, str]:
    """Module-level string constants of ``dispatcharr_client``.

    Resolved by importing the module (rather than re-deriving them from the AST)
    so a path constant assembled from other constants still resolves. The three
    path constants that exist today (``_USER_AGENTS_PATH``,
    ``_CORE_SETTINGS_PATH``, ``_DVR_RECURRING_RULES_PATH``) are plain literals.
    """
    return {
        name: value
        for name, value in vars(dispatcharr_client).items()
        if isinstance(value, str) and not name.startswith("__")
    }


def _resolve_path_expression(
    expression: ast.expr, annotations: Mapping[str, str], constants: Mapping[str, str]
) -> tuple[str, tuple[Optional[str], ...]]:
    """Resolve a path expression to a wildcard template + interpolated types.

    ``f"/api/m3u/accounts/{account_id}/"`` with ``account_id: int`` becomes
    ``("/api/m3u/accounts/{}/", ("int",))``.

    Raises:
        _Unresolvable: the expression is not a literal, a known module constant,
            or an f-string built from those plus simple name interpolations.
    """
    if isinstance(expression, ast.Constant) and isinstance(expression.value, str):
        return expression.value, ()

    if isinstance(expression, ast.Name):
        if expression.id in constants:
            return constants[expression.id], ()
        raise _Unresolvable(
            f"name {expression.id!r} is not a module-level string constant"
        )

    if isinstance(expression, ast.JoinedStr):
        rendered: list[str] = []
        annotated: list[Optional[str]] = []
        for part in expression.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                rendered.append(part.value)
                continue
            if not isinstance(part, ast.FormattedValue):
                raise _Unresolvable("f-string contains a non-string literal part")
            inner = part.value
            if isinstance(inner, ast.Name) and inner.id in constants:
                rendered.append(constants[inner.id])
                continue
            if (
                isinstance(inner, ast.Attribute)
                and inner.attr == "base_url"
                and isinstance(inner.value, ast.Name)
                and inner.value.id == "self"
            ):
                # ``self.base_url`` is the instance origin, not part of the path.
                rendered.append("")
                continue
            if isinstance(inner, ast.Name):
                rendered.append("{}")
                annotated.append(annotations.get(inner.id))
                continue
            raise _Unresolvable(
                f"f-string interpolates a non-name expression: {ast.unparse(inner)}"
            )
        return "".join(rendered), tuple(annotated)

    raise _Unresolvable("path expression is neither a literal, a constant, nor an f-string")


def _argument_annotations(function: ast.AST) -> dict[str, str]:
    arguments = function.args
    annotations: dict[str, str] = {}
    for argument in (
        list(arguments.posonlyargs) + list(arguments.args) + list(arguments.kwonlyargs)
    ):
        if argument.annotation is not None:
            annotations[argument.arg] = ast.unparse(argument.annotation)
    return annotations


def extract_client_calls(
    source: str, constants: Optional[Mapping[str, str]] = None
) -> ExtractionResult:
    """Derive every Dispatcharr call the client can issue from its source text.

    Three kinds of call site are extracted:

    1. ``self._request("<METHOD>", <path expression>, ...)`` — the shared path.
    2. ``self._get_json_bounded(<path expression>, ...)`` — the bounded-stream
       helper, which forwards its first argument to httpx as a GET.
    3. ``self._client.<verb>(<url expression>, ...)`` — the two raw login /
       token-refresh posts, whose ``f"{self.base_url}/..."`` prefix is stripped.

    Anything else that touches ``self._client`` is recorded in
    ``raw_call_sites`` for the plumbing-registry test; anything unresolvable is
    recorded in ``unresolved`` so the caller can fail loudly.
    """
    constants = {} if constants is None else constants
    tree = ast.parse(source)
    result = ExtractionResult()

    class_node = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == _CLIENT_CLASS_NAME
        ),
        None,
    )
    if class_node is None:
        raise AssertionError(
            f"{_CLIENT_CLASS_NAME} is not defined in the parsed source — the "
            "extractor is looking at the wrong module or the class was renamed."
        )

    for method_node in class_node.body:
        if not isinstance(method_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        owner = method_node.name
        annotations = _argument_annotations(method_node)

        for node in ast.walk(method_node):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            func = node.func

            is_self_call = isinstance(func.value, ast.Name) and func.value.id == "self"
            is_raw_client_call = (
                isinstance(func.value, ast.Attribute)
                and func.value.attr == "_client"
                and isinstance(func.value.value, ast.Name)
                and func.value.value.id == "self"
            )

            if is_self_call and func.attr == "_request":
                _extract_request_call(node, owner, annotations, constants, result)
            elif is_self_call and func.attr in _PATH_FORWARDING_HELPERS:
                _extract_forwarded_call(
                    node,
                    _PATH_FORWARDING_HELPERS[func.attr],
                    owner,
                    annotations,
                    constants,
                    result,
                )
            elif is_raw_client_call:
                result.raw_call_sites.append(RawCallSite(owner, func.attr, node.lineno))
                if func.attr in _RAW_VERB_ATTRS:
                    _extract_forwarded_call(
                        node, func.attr.upper(), owner, annotations, constants, result
                    )

    return result


def _extract_request_call(
    node: ast.Call,
    owner: str,
    annotations: Mapping[str, str],
    constants: Mapping[str, str],
    result: ExtractionResult,
) -> None:
    if len(node.args) < 2:
        result.unresolved.append(
            UnresolvedExpression(
                owner, node.lineno, ast.unparse(node), "_request called without (method, path)"
            )
        )
        return
    method_node = node.args[0]
    if not (isinstance(method_node, ast.Constant) and isinstance(method_node.value, str)):
        result.unresolved.append(
            UnresolvedExpression(
                owner,
                node.lineno,
                ast.unparse(method_node),
                "HTTP method is not a string literal",
            )
        )
        return
    _extract_forwarded_call(
        node, method_node.value.upper(), owner, annotations, constants, result, path_index=1
    )


def _extract_forwarded_call(
    node: ast.Call,
    method: str,
    owner: str,
    annotations: Mapping[str, str],
    constants: Mapping[str, str],
    result: ExtractionResult,
    path_index: int = 0,
) -> None:
    if len(node.args) <= path_index:
        result.unresolved.append(
            UnresolvedExpression(
                owner, node.lineno, ast.unparse(node), "call has no positional path argument"
            )
        )
        return
    path_node = node.args[path_index]
    try:
        template, parameter_annotations = _resolve_path_expression(
            path_node, annotations, constants
        )
    except _Unresolvable as exc:
        result.unresolved.append(
            UnresolvedExpression(owner, node.lineno, ast.unparse(path_node), str(exc))
        )
        return
    result.calls.append(
        ClientCall(method, template, parameter_annotations, owner, node.lineno)
    )


# ---------------------------------------------------------------------------
# The sweep itself — pure checks over (calls, manifest)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Mismatch:
    kind: str
    call: ClientCall
    detail: str

    def describe(self) -> str:
        return f"  [{self.kind}] {self.call.describe()}\n      {self.detail}"


def check_paths_and_methods(
    calls: Sequence[ClientCall],
    manifest_paths: Mapping[str, dict],
    exemptions: frozenset = _SELF_DESCRIBING_EXEMPTIONS,
) -> list[Mismatch]:
    """Every call's template must exist upstream and allow its method."""
    mismatches: list[Mismatch] = []
    for call in calls:
        if (call.method, call.template) in exemptions:
            continue
        entry = manifest_paths.get(call.template)
        if entry is None:
            mismatches.append(
                Mismatch(
                    "PATH NOT IN SCHEMA",
                    call,
                    "no upstream path matches this template "
                    "(this is exactly how lsa0s shipped: /api/dvr/rules/ existed nowhere)",
                )
            )
            continue
        methods = entry.get("methods", {})
        if call.method not in methods:
            mismatches.append(
                Mismatch(
                    "METHOD NOT ALLOWED",
                    call,
                    f"upstream {entry.get('openapi_path')} allows "
                    f"{sorted(methods) or '<none>'}",
                )
            )
    return mismatches


def path_parameter_conflict(
    annotation: Optional[str], parameter: Mapping[str, object]
) -> Optional[str]:
    """Return why an interpolated value cannot satisfy a path parameter, or None.

    The check is deliberately asymmetric, because the two directions carry very
    different signal:

    * An ``int``-annotated interpolation against an upstream parameter that
      declares a ``format`` (``string``/``uuid`` being the shape a primary-key
      migration produces) is REAL drift — the client would build a URL upstream
      can no longer route. Dispatcharr 0.28.2 already serves five ``uuid`` path
      parameters, so this check is not hypothetical.
    * An ``int``-annotated interpolation against a bare ``string`` parameter is
      NOT drift. drf-spectacular emits an untyped ``string`` for path converters
      that carry no declared type (custom ``@action`` routes and the ``/proxy/``
      surface); twelve of the parameters ECM interpolates are like that on
      0.28.2 and every one of them is an integer pk in practice. Flagging those
      would be twelve permanent false positives — noise that trains readers to
      ignore the sweep.

    An unannotated interpolation yields no signal either way and is skipped.
    """
    if annotation not in _INT_ANNOTATIONS:
        return None
    declared_format = parameter.get("format")
    if declared_format and declared_format not in _INT_COMPATIBLE_FORMATS:
        return (
            f"client interpolates an {annotation}, but upstream parameter "
            f"{parameter.get('name')!r} declares format={declared_format!r} "
            f"(type={parameter.get('type')!r})"
        )
    declared_type = parameter.get("type")
    if declared_type not in (None, "integer", "number", "string"):
        return (
            f"client interpolates an {annotation}, but upstream parameter "
            f"{parameter.get('name')!r} declares type={declared_type!r}"
        )
    return None


def check_path_parameters(
    calls: Sequence[ClientCall],
    manifest_paths: Mapping[str, dict],
    exemptions: frozenset = _SELF_DESCRIBING_EXEMPTIONS,
) -> list[Mismatch]:
    """Path-parameter count and type must line up with the recorded manifest."""
    mismatches: list[Mismatch] = []
    for call in calls:
        if (call.method, call.template) in exemptions:
            continue
        entry = manifest_paths.get(call.template)
        if entry is None or call.method not in entry.get("methods", {}):
            # Reported by check_paths_and_methods; nothing further to say here.
            continue
        parameters = entry["methods"][call.method].get("path_parameters", [])
        if len(parameters) != len(call.parameter_annotations):
            mismatches.append(
                Mismatch(
                    "PATH PARAMETER COUNT",
                    call,
                    f"client interpolates {len(call.parameter_annotations)} value(s); "
                    f"upstream {entry.get('openapi_path')} declares {len(parameters)}",
                )
            )
            continue
        for annotation, parameter in zip(call.parameter_annotations, parameters):
            conflict = path_parameter_conflict(annotation, parameter)
            if conflict is not None:
                mismatches.append(Mismatch("PATH PARAMETER TYPE", call, conflict))
    return mismatches


def format_sweep_failure(mismatches: Sequence[Mismatch], capture: Mapping[str, object]) -> str:
    """Build the operator-facing failure message.

    States BOTH possible causes without presuming one, and forbids the escape
    hatch that would hide the finding.
    """
    lines = [
        f"{len(mismatches)} Dispatcharr client call(s) do not match the recorded "
        "OpenAPI paths manifest:",
        "",
        *[mismatch.describe() for mismatch in mismatches],
        "",
        "Manifest: backend/tests/fixtures/dispatcharr_openapi_paths_manifest.json",
        f"  recorded from Dispatcharr {capture.get('dispatcharr_version')} "
        f"on {capture.get('captured_at')} ({capture.get('path_count')} paths).",
        "",
        "There are exactly two causes, and you must decide which one this is:",
        "",
        "  (1) THE MANIFEST IS STALE. The instance ECM targets runs a newer "
        "Dispatcharr than the one recorded above, and the endpoint moved or was "
        "renamed upstream. Re-record against the version you now support:",
        "        python scripts/record_dispatcharr_openapi_manifest.py \\",
        "            --schema <raw GET /api/schema/?format=json> \\",
        "            --dispatcharr-version <GET /api/core/version/>",
        "      Then re-run this test and confirm the client still works live.",
        "",
        "  (2) THE CLIENT IS WRONG. The path was guessed, mistyped, or copied by "
        "analogy from a neighbouring resource and does not exist upstream. Fix "
        "the client. Prior art: enhancedchannelmanager-lsa0s, where "
        "/api/dvr/rules/ existed on no Dispatcharr version and returned the SPA "
        "shell as 200 text/html, so every DVR-rules backup silently exported a "
        "warning stub; and enhancedchannelmanager-q6xjl, where a key-string "
        "settings URL 404'd on every restore key.",
        "",
        "Do NOT xfail, skip, or add an allowlist entry to force CI green. Both "
        "causes have a real fix and the sweep exists because the class of bug it "
        "catches reached operators twice (ADR-014).",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(_MANIFEST_PATH.read_text())


@pytest.fixture(scope="module")
def extraction() -> ExtractionResult:
    return extract_client_calls(
        _CLIENT_SOURCE_PATH.read_text(), client_module_constants()
    )


# ---------------------------------------------------------------------------
# Extractor integrity — a partial sweep must never report green
# ---------------------------------------------------------------------------


def test_every_client_path_expression_resolves(extraction: ExtractionResult):
    """No path expression may be silently skipped — unresolvable ones fail loud."""
    assert not extraction.unresolved, (
        "The contract sweep could not statically resolve "
        f"{len(extraction.unresolved)} Dispatcharr path expression(s):\n"
        + "\n".join(f"  {item.describe()}" for item in extraction.unresolved)
        + "\n\nA path the extractor cannot read is a path the sweep does not "
        "check. Either express it as a literal / module constant / simple "
        "f-string, or extend the extractor — never leave it unresolved."
    )


def test_no_unreviewed_raw_http_call_sites(extraction: ExtractionResult):
    """Every direct httpx call site is either extracted or reviewed plumbing."""
    unreviewed = [
        site
        for site in extraction.raw_call_sites
        if site.attribute in _RAW_HTTP_ATTRS
        and site.attribute not in _RAW_VERB_ATTRS
        and (site.owner, site.attribute) not in _REVIEWED_PLUMBING_CALL_SITES
    ]
    assert not unreviewed, (
        "New raw httpx call site(s) in DispatcharrClient that the sweep does not "
        "cover:\n"
        + "\n".join(
            f"  self._client.{site.attribute}(...) in "
            f"DispatcharrClient.{site.owner} (line {site.lineno})"
            for site in unreviewed
        )
        + "\n\nEither route the call through self._request (preferred), or — if "
        "it is genuinely plumbing that forwards a path its own callers supply — "
        "add it to _REVIEWED_PLUMBING_CALL_SITES and to _PATH_FORWARDING_HELPERS "
        "so its call sites are still swept."
    )


def test_extractor_call_volume_canary(extraction: ExtractionResult):
    """A broken extractor must not pass by finding nothing.

    The client had 95 resolvable call sites over 90 distinct (method, template)
    pairs when the sweep landed. The floors are deliberately well below that so
    ordinary refactoring does not trip them, while a regression that silently
    stops extracting (a renamed helper, a changed call convention) cannot slip
    through as a vacuously green sweep.
    """
    assert len(extraction.calls) >= 50, (
        f"Extractor found only {len(extraction.calls)} Dispatcharr call sites; "
        "expected at least 50. It has almost certainly stopped matching the "
        "client's call convention rather than the client having shrunk."
    )
    assert len(extraction.templates) >= 40, (
        f"Extractor found only {len(extraction.templates)} distinct (method, "
        "template) pairs; expected at least 40."
    )


def test_extractor_covers_paths_from_all_three_call_conventions(
    extraction: ExtractionResult,
):
    """The literal, module-constant and raw-login conventions are all extracted."""
    templates = extraction.templates
    # 1. plain literal via self._request
    assert ("GET", "/api/channels/channels/") in templates
    # 2. module-level constant + f-string interpolation
    assert ("DELETE", "/api/core/useragents/{}/") in templates
    # 3. raw self._client.post with the self.base_url prefix stripped
    assert ("POST", "/api/accounts/token/") in templates
    assert ("POST", "/api/accounts/token/refresh/") in templates
    # 4. the bounded-stream helper's forwarded path
    assert ("GET", "/api/epg/epgdata/") in templates


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------


def test_every_client_call_matches_the_recorded_manifest(
    extraction: ExtractionResult, manifest: dict
):
    """THE SWEEP: every (method, template) exists upstream with that method."""
    mismatches = check_paths_and_methods(extraction.calls, manifest["paths"])
    assert not mismatches, format_sweep_failure(mismatches, manifest["capture"])


def test_path_parameter_types_match_the_recorded_manifest(
    extraction: ExtractionResult, manifest: dict
):
    """Path-parameter arity and type line up (guards an int -> UUID pk migration)."""
    mismatches = check_path_parameters(extraction.calls, manifest["paths"])
    assert not mismatches, format_sweep_failure(mismatches, manifest["capture"])


def test_schema_endpoint_exemption_is_still_required(manifest: dict):
    """The single exemption self-invalidates once upstream documents that route.

    ``GET /api/schema/`` is the only call the manifest cannot contain, because
    drf-spectacular does not describe its own schema route inside the document
    it generates. If a future Dispatcharr does document it, this test fails and
    the exemption must be DELETED — the sweep must never accumulate an allowlist
    that outlives its reason.
    """
    assert _SELF_DESCRIBING_EXEMPTIONS == frozenset({("GET", "/api/schema/")}), (
        "The sweep grew a new exemption. Exemptions hide exactly the drift this "
        "test exists to catch; justify it in ADR-014 or remove it."
    )
    assert "/api/schema/" not in manifest["paths"], (
        "The recorded manifest now documents /api/schema/, so the exemption in "
        "_SELF_DESCRIBING_EXEMPTIONS is obsolete — delete it and let the sweep "
        "check that path like any other."
    )


def test_manifest_provenance_is_recorded(manifest: dict):
    """The manifest must be able to say where and when it came from."""
    capture = manifest["capture"]
    assert capture["dispatcharr_version"], "manifest records no Dispatcharr version"
    assert capture["captured_at"], "manifest records no capture date"
    assert capture["path_count"] == len(manifest["paths"])
    assert capture["recorded_by"] == "scripts/record_dispatcharr_openapi_manifest.py"


# ---------------------------------------------------------------------------
# Negative coverage — every check above is proven able to FAIL
# ---------------------------------------------------------------------------


def _call(method: str, template: str, annotations=(), owner="fake_method") -> ClientCall:
    return ClientCall(method, template, tuple(annotations), owner, 1)


def test_sweep_detects_a_path_that_upstream_does_not_have(manifest: dict):
    """The lsa0s shape: a plausible-but-imaginary path."""
    mismatches = check_paths_and_methods(
        [_call("GET", "/api/dvr/rules/", owner="get_dvr_rules")], manifest["paths"]
    )
    assert [mismatch.kind for mismatch in mismatches] == ["PATH NOT IN SCHEMA"]
    message = format_sweep_failure(mismatches, manifest["capture"])
    assert "GET /api/dvr/rules/" in message
    assert "get_dvr_rules" in message
    assert manifest["capture"]["dispatcharr_version"] in message
    assert "THE MANIFEST IS STALE" in message and "THE CLIENT IS WRONG" in message


def test_sweep_detects_a_method_upstream_does_not_allow(manifest: dict):
    """The path exists, but the verb does not — e.g. DELETE on a list route."""
    mismatches = check_paths_and_methods(
        [_call("DELETE", "/api/core/version/")], manifest["paths"]
    )
    assert [mismatch.kind for mismatch in mismatches] == ["METHOD NOT ALLOWED"]
    assert "GET" in mismatches[0].detail


def test_sweep_detects_an_integer_id_against_a_uuid_path_parameter(manifest: dict):
    """The int -> UUID primary-key migration this check exists to catch.

    Uses a REAL uuid-typed path parameter from the recorded 0.28.2 document, so
    the check is proven against the upstream shape rather than a hand-built one.
    """
    uuid_templates = [
        template
        for template, entry in manifest["paths"].items()
        for operation in entry["methods"].values()
        for parameter in operation["path_parameters"]
        if parameter.get("format") == "uuid"
    ]
    assert uuid_templates, (
        "The recorded manifest has no uuid path parameter, so this negative test "
        "would be vacuous — pick another incompatible format."
    )
    template = uuid_templates[0]
    method = next(
        method
        for method, operation in manifest["paths"][template]["methods"].items()
        if any(p.get("format") == "uuid" for p in operation["path_parameters"])
    )
    parameter_count = len(manifest["paths"][template]["methods"][method]["path_parameters"])

    mismatches = check_path_parameters(
        [_call(method, template, ["int"] * parameter_count)], manifest["paths"]
    )
    assert [mismatch.kind for mismatch in mismatches] == ["PATH PARAMETER TYPE"]
    assert "uuid" in mismatches[0].detail


def test_sweep_detects_a_wrong_number_of_path_parameters(manifest: dict):
    mismatches = check_path_parameters(
        [_call("GET", "/api/channels/channels/{}/", [])], manifest["paths"]
    )
    assert [mismatch.kind for mismatch in mismatches] == ["PATH PARAMETER COUNT"]


def test_bare_string_path_parameters_do_not_produce_false_positives():
    """An untyped upstream ``string`` converter is compatible with an int id."""
    assert path_parameter_conflict("int", {"name": "channel_id", "type": "string"}) is None
    assert path_parameter_conflict("int", {"name": "id", "type": "integer"}) is None
    assert path_parameter_conflict(None, {"name": "id", "format": "uuid"}) is None
    assert (
        path_parameter_conflict("int", {"name": "id", "type": "string", "format": "uuid"})
        is not None
    )


_SYNTHETIC_CLIENT_HEADER = "class DispatcharrClient:\n"


def test_extractor_reports_an_unresolvable_path_expression():
    """A computed path must fail loudly, never be silently dropped."""
    source = _SYNTHETIC_CLIENT_HEADER + (
        "    async def get_thing(self, kind):\n"
        "        return await self._request('GET', build_path(kind))\n"
    )
    result = extract_client_calls(source)
    assert result.calls == []
    assert len(result.unresolved) == 1
    assert result.unresolved[0].owner == "get_thing"
    assert "neither a literal" in result.unresolved[0].reason


def test_extractor_reports_an_unresolvable_f_string_interpolation():
    source = _SYNTHETIC_CLIENT_HEADER + (
        "    async def get_thing(self, kind):\n"
        "        return await self._request('GET', f'/api/{kind.name}/')\n"
    )
    result = extract_client_calls(source)
    assert result.calls == []
    assert "non-name expression" in result.unresolved[0].reason


def test_extractor_reports_a_non_literal_http_method():
    source = _SYNTHETIC_CLIENT_HEADER + (
        "    async def do_thing(self, verb):\n"
        "        return await self._request(verb, '/api/channels/channels/')\n"
    )
    result = extract_client_calls(source)
    assert result.calls == []
    assert "not a string literal" in result.unresolved[0].reason


def test_extractor_reports_an_unknown_module_constant():
    source = _SYNTHETIC_CLIENT_HEADER + (
        "    async def get_thing(self):\n"
        "        return await self._request('GET', _SOME_PATH)\n"
    )
    result = extract_client_calls(source, {})
    assert result.calls == []
    assert "module-level string constant" in result.unresolved[0].reason

    resolved = extract_client_calls(source, {"_SOME_PATH": "/api/core/version/"})
    assert resolved.unresolved == []
    assert resolved.templates == {("GET", "/api/core/version/")}


def test_extractor_flags_a_new_raw_httpx_call_site():
    """A new raw call site is visible to the plumbing-registry test."""
    source = _SYNTHETIC_CLIENT_HEADER + (
        "    async def stream_thing(self, path):\n"
        "        return await self._client.stream('GET', path)\n"
    )
    result = extract_client_calls(source)
    unreviewed = [
        site
        for site in result.raw_call_sites
        if site.attribute in _RAW_HTTP_ATTRS
        and site.attribute not in _RAW_VERB_ATTRS
        and (site.owner, site.attribute) not in _REVIEWED_PLUMBING_CALL_SITES
    ]
    assert [(site.owner, site.attribute) for site in unreviewed] == [
        ("stream_thing", "stream")
    ]
