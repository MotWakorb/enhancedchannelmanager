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

# Attribute names on ``self._client`` (the raw httpx.AsyncClient) that can put a
# request on the wire. Every call site using one of these is either extracted as
# a client call (the verb methods) or must appear in the reviewed plumbing set
# below — never silently ignored.
_RAW_HTTP_ATTRS = frozenset(
    {"get", "post", "put", "patch", "delete", "request", "send", "build_request", "stream"}
)
_RAW_VERB_ATTRS = frozenset({"get", "post", "put", "patch", "delete"})

# httpx client classes whose CONSTRUCTION opens a transport the extractor cannot
# follow. Building one inside this module (``async with httpx.AsyncClient() as
# c: await c.get(url)``) would otherwise issue requests entirely outside the
# sweep's view, so every construction is a call site that must be reviewed.
_HTTPX_CLIENT_CLASSES = frozenset({"AsyncClient", "Client"})

# Raw-httpx seams that carry no extractable URL template of their own because
# they are the client's own plumbing: ``__init__`` builds the transport,
# ``_request`` forwards a method + path it was given, and ``_get_json_bounded``
# forwards a path parameter whose call sites ARE extracted (see
# ``_PATH_FORWARDING_HELPERS``). Reviewed once, recorded here; anything NEW that
# reaches httpx directly fails ``test_no_unreviewed_raw_http_call_sites`` until
# a human classifies it.
#
# Owners are CLASS-QUALIFIED. That is load-bearing: the extractor walks every
# class in the module, so an identically-named method on a second class must not
# inherit this one's review.
_REVIEWED_RAW_CALL_SITES = frozenset(
    {
        ("DispatcharrClient.__init__", "httpx.AsyncClient(...)"),
        ("DispatcharrClient._request", "self._client.request"),
        ("DispatcharrClient._get_json_bounded", "self._client.build_request"),
        ("DispatcharrClient._get_json_bounded", "self._client.send"),
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

# The ONE module this sweep reads. Everything about the extraction is scoped to
# it: ``_CLIENT_SOURCE_PATH`` is its source, and a base class living anywhere
# else takes its methods out of the sweep's view entirely (see
# ``bases_outside_the_swept_module``).
_SWEPT_MODULE = dispatcharr_client.__name__

# Owner prefix for a module-level function, so EVERY owner is qualified. The
# reviewed-seam registry is keyed on the owner string, and an unqualified
# ``fetch_version`` would be ambiguous with a method of the same name.
_MODULE_OWNER_PREFIX = "<module>."

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
            f"({self.owner}, {_CLIENT_SOURCE_PATH.name}:{self.lineno})"
        )


@dataclass(frozen=True)
class UnresolvedExpression:
    """A path/method expression the extractor could not statically resolve."""

    owner: str
    lineno: int
    expression: str
    reason: str

    def describe(self) -> str:
        return f"{self.owner} line {self.lineno}: {self.reason} -- {self.expression}"


@dataclass(frozen=True)
class RawCallSite:
    """An httpx seam whose URL the extractor cannot read.

    ``marker`` names the seam — ``self._client.request``, ``httpx.AsyncClient(...)``,
    or ``alias of self._client (c)`` — and is matched against
    ``_REVIEWED_RAW_CALL_SITES`` together with the class-qualified ``owner``.
    """

    owner: str
    marker: str
    lineno: int

    def describe(self) -> str:
        return f"{self.marker} in {self.owner} ({_CLIENT_SOURCE_PATH.name}:{self.lineno})"


@dataclass
class ExtractionResult:
    calls: list[ClientCall] = field(default_factory=list)
    unresolved: list[UnresolvedExpression] = field(default_factory=list)
    raw_call_sites: list[RawCallSite] = field(default_factory=list)

    @property
    def templates(self) -> set[tuple[str, str]]:
        return {(call.method, call.template) for call in self.calls}


def unreviewed_raw_call_sites(result: ExtractionResult) -> list[RawCallSite]:
    """Raw httpx seams that nobody has classified yet."""
    return [
        site
        for site in result.raw_call_sites
        if (site.owner, site.marker) not in _REVIEWED_RAW_CALL_SITES
    ]


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


def _is_self_client(expression: ast.expr) -> bool:
    """True for the expression ``self._client``."""
    return (
        isinstance(expression, ast.Attribute)
        and expression.attr == "_client"
        and isinstance(expression.value, ast.Name)
        and expression.value.id == "self"
    )


def _httpx_client_construction(func: ast.expr) -> Optional[str]:
    """Return a marker if ``func`` constructs an httpx client, else ``None``."""
    if isinstance(func, ast.Attribute) and func.attr in _HTTPX_CLIENT_CLASSES:
        if isinstance(func.value, ast.Name) and func.value.id == "httpx":
            return f"httpx.{func.attr}(...)"
    if isinstance(func, ast.Name) and func.id in _HTTPX_CLIENT_CLASSES:
        return f"httpx.{func.id}(...)"
    return None


def _collect_units(tree: ast.Module) -> list[tuple[str, ast.AST]]:
    """Return ``(qualified name, function node)`` for every function.

    Walks the module body recursively through EVERY ``ClassDef``, not just one
    named class: PR #773's review proved that a routine refactor — moving nine
    methods onto a mixin base, or adding a second request-issuing class —
    silently dropped those calls from the sweep while it still reported green.

    Owners are always qualified: a method is ``Class.method`` (nested classes
    accumulate, ``Outer.Inner.method``) and a module-level function is
    ``<module>.function``. That is load-bearing for the reviewed-seam registry,
    which is keyed on the owner string.

    Nested functions are NOT separate units; they are reached by ``ast.walk``
    from their enclosing function, so a call inside ``_get_json_bounded``'s
    local ``send()`` is still attributed to ``_get_json_bounded``.
    """
    units: list[tuple[str, ast.AST]] = []

    def visit(body, prefix: str) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                units.append(((prefix or _MODULE_OWNER_PREFIX) + node.name, node))
            elif isinstance(node, ast.ClassDef):
                visit(node.body, f"{prefix}{node.name}.")

    visit(tree.body, "")
    return units


def classes_defined_in_the_swept_module() -> list[type]:
    """Every class ``dispatcharr_client`` itself defines (not ones it imports)."""
    return [
        value
        for value in vars(dispatcharr_client).values()
        if isinstance(value, type) and value.__module__ == _SWEPT_MODULE
    ]


def bases_outside_the_swept_module(cls: type) -> list[type]:
    """Bases of ``cls`` whose methods this sweep structurally cannot see.

    The extraction reads exactly one source file. A base class defined in a
    SIBLING module — ``from dispatcharr_client_m3u import _M3UMixin``, the
    natural next refactor for a 2,100-line file — takes every method it carries
    out of the sweep's view, and nothing else notices: the canary floors sit far
    below the real counts, and each surviving call still matches the manifest.
    Review measured the damage on the real module: one such move dropped 31 of
    93 ``(method, template)`` pairs (98 call sites down to 67) with the whole
    suite green.

    This is the structural half of ADR-014's property 3. ``_collect_units``
    covers the in-module case (a mixin declared alongside the client); this
    covers the cross-module one.
    """
    return [
        base
        for base in cls.__mro__
        if base is not object and base.__module__ != _SWEPT_MODULE
    ]


def format_foreign_base_failure(cls: type, offenders: Sequence[type]) -> str:
    lines = [
        f"{cls.__name__} inherits from {len(offenders)} class(es) defined outside "
        f"{_SWEPT_MODULE}:",
        "",
        *[f"  {base.__module__}.{base.__qualname__}" for base in offenders],
        "",
        f"The contract sweep reads ONE module — {_CLIENT_SOURCE_PATH.name} — so every "
        "Dispatcharr call those bases issue is now unswept, silently. Nothing else "
        "catches this: the canary floors are far below the real counts and the calls "
        "that remain still match the manifest.",
        "",
        "Fix it one of two ways, and do not weaken this assertion instead:",
        "  (1) Move the base back into dispatcharr_client.py (the sweep already "
        "walks every class in the module, mixins included), or",
        "  (2) Extend the sweep to read the additional module(s): make the source "
        "path a list, extract from each, and update _SWEPT_MODULE / this check to "
        "match the new set.",
    ]
    return "\n".join(lines)


def extract_client_calls(
    source: str, constants: Optional[Mapping[str, str]] = None
) -> ExtractionResult:
    """Derive every Dispatcharr call the module can issue from its source text.

    Every function in the module is walked — top-level functions and the methods
    of every class at any nesting depth — so coverage does not depend on the
    calls living in one particular class.

    Three kinds of call site are extracted:

    1. ``self._request("<METHOD>", <path expression>, ...)`` — the shared path.
    2. ``self._get_json_bounded(<path expression>, ...)`` — the bounded-stream
       helper, which forwards its first argument to httpx as a GET.
    3. ``self._client.<verb>(<url expression>, ...)`` — the raw login /
       token-refresh posts, whose ``f"{self.base_url}/..."`` prefix is stripped.

    Three kinds of *seam* are recorded in ``raw_call_sites`` instead, because
    their URL is not readable here; each must be registered in
    ``_REVIEWED_RAW_CALL_SITES`` or it fails a test:

    * a non-verb call on ``self._client`` (``request``, ``send``, ``build_request``…),
    * constructing an httpx client (``async with httpx.AsyncClient() as c: …``),
    * binding ``self._client`` to a local name (``c = self._client``), which
      would otherwise let ``await c.get(url)`` slip past the attribute match.

    Anything unresolvable is recorded in ``unresolved`` so the caller can fail
    loudly.
    """
    constants = {} if constants is None else constants
    tree = ast.parse(source)
    result = ExtractionResult()

    for owner, function_node in _collect_units(tree):
        annotations = _argument_annotations(function_node)

        # Local names bound to self._client are an escape hatch out of the
        # attribute match below, so the BINDING itself is the reported seam —
        # it fails regardless of how the alias is later used.
        aliases: set[str] = set()
        for node in ast.walk(function_node):
            targets: list[ast.expr] = []
            if isinstance(node, ast.Assign) and _is_self_client(node.value):
                targets = list(node.targets)
            elif (
                isinstance(node, ast.AnnAssign)
                and node.value is not None
                and _is_self_client(node.value)
            ):
                targets = [node.target]
            elif isinstance(node, ast.NamedExpr) and _is_self_client(node.value):
                targets = [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    aliases.add(target.id)
                    result.raw_call_sites.append(
                        RawCallSite(
                            owner, f"alias of self._client ({target.id})", node.lineno
                        )
                    )

        for node in ast.walk(function_node):
            if not isinstance(node, ast.Call):
                continue
            func = node.func

            construction = _httpx_client_construction(func)
            if construction is not None:
                result.raw_call_sites.append(RawCallSite(owner, construction, node.lineno))
                continue

            if not isinstance(func, ast.Attribute):
                continue

            is_self_call = isinstance(func.value, ast.Name) and func.value.id == "self"

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
            elif _is_self_client(func.value):
                if func.attr in _RAW_VERB_ATTRS:
                    _extract_forwarded_call(
                        node, func.attr.upper(), owner, annotations, constants, result
                    )
                elif func.attr in _RAW_HTTP_ATTRS:
                    result.raw_call_sites.append(
                        RawCallSite(owner, f"self._client.{func.attr}", node.lineno)
                    )
            elif (
                isinstance(func.value, ast.Name)
                and func.value.id in aliases
                and func.attr in _RAW_HTTP_ATTRS
            ):
                result.raw_call_sites.append(
                    RawCallSite(
                        owner, f"{func.value.id}.{func.attr} (alias of self._client)",
                        node.lineno,
                    )
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
    """Every httpx seam THIS MODULE opens is extracted or registered as reviewed.

    The three seam shapes checked are a non-verb call on ``self._client``, an
    httpx client construction, and a local name bound to ``self._client``. This
    is a claim about ``dispatcharr_client.py`` only — a request issued from
    another module is outside the sweep's scope (ADR-014), not something this
    test can see.
    """
    unreviewed = unreviewed_raw_call_sites(extraction)
    assert not unreviewed, (
        "New raw httpx seam(s) in dispatcharr_client.py that the sweep cannot "
        "read a URL from:\n"
        + "\n".join(f"  {site.describe()}" for site in unreviewed)
        + "\n\nEither route the call through self._request (preferred), or — if "
        "it is genuinely plumbing that forwards a path its own callers supply — "
        "add it to _REVIEWED_RAW_CALL_SITES and, when it forwards a path, to "
        "_PATH_FORWARDING_HELPERS so its call sites are still swept."
    )


def test_extractor_call_volume_canary(extraction: ExtractionResult):
    """A broken extractor must not pass by finding nothing.

    The floors are deliberately far below the actual count (which the assertion
    messages report, so this docstring cannot go stale) — they exist to catch a
    total extraction failure, e.g. a renamed helper or a changed call
    convention, not to measure coverage.

    They are NOT a coverage guard and must not be mistaken for one: PR #773's
    review dropped nine real calls with a routine refactor and these floors did
    not move. That class of silent shrinkage is caught structurally instead, by
    walking every class and top-level function in the module and by flagging
    every unreadable httpx seam — see ``_collect_units`` and
    ``test_no_unreviewed_raw_http_call_sites``.
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


def test_extractor_covers_every_call_convention(extraction: ExtractionResult):
    """Each convention the client actually uses produces an extracted template."""
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


def test_extraction_owners_are_qualified(extraction: ExtractionResult):
    """Every owner is qualified, so no two call sites can collide in the registry.

    A method is ``Class.method`` (so a second class cannot inherit another's
    review entry) and a module-level function is ``<module>.function``. The
    qualification is what makes ``_REVIEWED_RAW_CALL_SITES`` safe to key on the
    owner string; a bare ``fetch_version`` would be ambiguous with a method of
    that name.
    """
    owners = {call.owner for call in extraction.calls}
    assert "DispatcharrClient.get_channels" in owners
    assert all("." in owner for owner in owners), (
        f"unqualified owner(s) in {sorted(owners)[:5]} — the reviewed-seam "
        "registry is keyed on the owner string and would mis-match"
    )


def test_no_swept_class_inherits_from_outside_the_module(extraction: ExtractionResult):
    """ADR-014 property 3, structurally: coverage cannot leave via a sibling module.

    ``_collect_units`` already covers a mixin declared INSIDE
    ``dispatcharr_client.py``. This covers the other half — a base imported from
    a sibling module, which the single-module extraction cannot read at all.
    """
    classes = classes_defined_in_the_swept_module()
    assert dispatcharr_client.DispatcharrClient in classes, (
        "the class enumeration no longer finds DispatcharrClient, so this check "
        "would pass vacuously — fix classes_defined_in_the_swept_module()"
    )
    for cls in classes:
        offenders = bases_outside_the_swept_module(cls)
        assert not offenders, format_foreign_base_failure(cls, offenders)


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
    assert result.unresolved[0].owner == "DispatcharrClient.get_thing"
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
    """A new raw call site is visible to the reviewed-seam test."""
    source = _SYNTHETIC_CLIENT_HEADER + (
        "    async def stream_thing(self, path):\n"
        "        return await self._client.stream('GET', path)\n"
    )
    unreviewed = unreviewed_raw_call_sites(extract_client_calls(source))
    assert [(site.owner, site.marker) for site in unreviewed] == [
        ("DispatcharrClient.stream_thing", "self._client.stream")
    ]


# ---------------------------------------------------------------------------
# Extractor completeness — refactors that must NOT silently shrink the sweep
# (PR #773 review, W2: each of these left the sweep green while dropping
# coverage, and the 50/40 canary floors were far too coarse to notice.)
# ---------------------------------------------------------------------------


def test_extractor_covers_methods_inherited_from_a_mixin_in_the_module():
    """Moving methods onto a mixin base must not drop them from the sweep."""
    source = (
        "class _M3UFilterMixin:\n"
        "    async def get_m3u_filters(self, account_id: int):\n"
        "        return await self._request('GET', f'/api/m3u/accounts/{account_id}/filters/')\n"
        "\n"
        "class DispatcharrClient(_M3UFilterMixin):\n"
        "    async def get_channels(self):\n"
        "        return await self._request('GET', '/api/channels/channels/')\n"
    )
    result = extract_client_calls(source)
    assert result.templates == {
        ("GET", "/api/m3u/accounts/{}/filters/"),
        ("GET", "/api/channels/channels/"),
    }
    assert {call.owner for call in result.calls} == {
        "_M3UFilterMixin.get_m3u_filters",
        "DispatcharrClient.get_channels",
    }


def test_extractor_covers_a_second_request_issuing_class():
    """A sibling class in the same module is swept too, not ignored."""
    source = (
        "class DispatcharrClient:\n"
        "    async def get_channels(self):\n"
        "        return await self._request('GET', '/api/channels/channels/')\n"
        "\n"
        "class DispatcharrAdminClient:\n"
        "    async def get_users(self):\n"
        "        return await self._request('GET', '/api/accounts/users/')\n"
    )
    result = extract_client_calls(source)
    assert ("GET", "/api/accounts/users/") in result.templates


def test_extractor_covers_a_top_level_function():
    """A module-level helper is swept, and its owner is qualified like a method.

    PR #773 review, N2: the owner used to be a bare ``fetch_version``, which
    contradicted ``test_extraction_owners_are_qualified`` — adding a module-level
    helper that issued a client call would have turned the sweep red with a
    message blaming the reviewed-seam registry for a legitimate refactor.
    """
    source = (
        "async def fetch_version(self):\n"
        "    return await self._request('GET', '/api/core/version/')\n"
    )
    result = extract_client_calls(source)
    assert result.templates == {("GET", "/api/core/version/")}
    assert result.calls[0].owner == "<module>.fetch_version"


class _BaseFromAnotherModule:
    """Stands in for a mixin moved to a sibling module (its ``__module__`` is this test)."""


def test_a_base_class_outside_the_swept_module_is_detected():
    """The cross-module refactor that silently dropped 31 of 93 pairs now fails.

    Defined here rather than in ``dispatcharr_client``, so this class's
    ``__module__`` is a different module — exactly the shape of
    ``from dispatcharr_client_m3u import _M3UMixin``.
    """

    class _RefactoredClient(_BaseFromAnotherModule):
        pass

    offenders = bases_outside_the_swept_module(_RefactoredClient)
    assert _BaseFromAnotherModule in offenders

    message = format_foreign_base_failure(_RefactoredClient, offenders)
    assert "_BaseFromAnotherModule" in message
    assert __name__ in message, "the failure must name WHERE the base now lives"
    assert "reads ONE module" in message
    assert "Move the base back" in message and "Extend the sweep" in message


def test_a_base_defined_in_the_swept_module_is_not_flagged():
    """The in-module mixin case stays legal — it is already covered by extraction."""
    assert bases_outside_the_swept_module(dispatcharr_client.DispatcharrClient) == []


def test_extractor_flags_a_local_alias_of_the_transport():
    """``c = self._client`` is an escape hatch and must be reported, not ignored."""
    source = _SYNTHETIC_CLIENT_HEADER + (
        "    async def get_thing(self, path):\n"
        "        c = self._client\n"
        "        return await c.get(path)\n"
    )
    unreviewed = unreviewed_raw_call_sites(extract_client_calls(source))
    markers = {site.marker for site in unreviewed}
    assert "alias of self._client (c)" in markers
    assert all(
        site.owner == "DispatcharrClient.get_thing" for site in unreviewed
    )


def test_extractor_flags_a_walrus_alias_of_the_transport():
    source = _SYNTHETIC_CLIENT_HEADER + (
        "    async def get_thing(self, path):\n"
        "        return await (c := self._client).get(path)\n"
    )
    markers = {
        site.marker for site in unreviewed_raw_call_sites(extract_client_calls(source))
    }
    assert "alias of self._client (c)" in markers


def test_extractor_flags_an_httpx_client_constructed_inside_the_module():
    """``async with httpx.AsyncClient() as c`` bypasses the transport entirely."""
    source = _SYNTHETIC_CLIENT_HEADER + (
        "    async def get_thing(self, url):\n"
        "        async with httpx.AsyncClient() as c:\n"
        "            return await c.get(url)\n"
    )
    unreviewed = unreviewed_raw_call_sites(extract_client_calls(source))
    assert [(site.owner, site.marker) for site in unreviewed] == [
        ("DispatcharrClient.get_thing", "httpx.AsyncClient(...)")
    ]


def test_reviewed_seams_are_matched_per_class_not_per_method_name():
    """An identically-named method on another class does not inherit a review."""
    source = (
        "class DispatcharrClient:\n"
        "    async def _request(self, method, path):\n"
        "        return await self._client.request(method, path)\n"
        "\n"
        "class RogueClient:\n"
        "    async def _request(self, method, path):\n"
        "        return await self._client.request(method, path)\n"
    )
    unreviewed = unreviewed_raw_call_sites(extract_client_calls(source))
    assert [(site.owner, site.marker) for site in unreviewed] == [
        ("RogueClient._request", "self._client.request")
    ]
