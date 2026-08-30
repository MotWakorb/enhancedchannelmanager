"""Static completeness guards for session-cookie transport policy.

Bead enhancedchannelmanager-04c0u.9 remediation.

These are pure source analysis — no app, no database, no I/O beyond reading
``.py`` files — so they belong in ``tests/unit/`` (they previously sat in
``tests/integration/``).

Two things changed besides the move:

* **Scope.** The guards parsed ``auth/routes.py`` only, so a cookie-emitting
  call site in any other module was invisible to them. They now walk every
  backend module.
* **Shape.** Neither guard covered a raw ``response.set_cookie`` — which is
  *the original defect's own shape*: the bug this bead fixed was a literal
  ``secure=False`` passed straight to ``set_cookie``. A guard that cannot see
  the defect it exists to prevent is not a guard.

The magic ``len(call_sites) == 5`` assertion is gone too. A legitimate new call
site failed it with a count mismatch whose obvious repair is editing the number,
which trains exactly the wrong reflex. The property — every site derives
``secure=`` from ``_auth_cookie_secure`` and threads a real ``request`` — holds
at any count, and a non-emptiness check keeps the guard from passing vacuously
if the walk ever stops finding anything.
"""

import ast
from pathlib import Path

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[2]
AUTH_ROUTES_SOURCE = BACKEND_ROOT / "auth" / "routes.py"

COOKIE_HELPERS = {"_set_access_cookie", "_set_auth_cookies", "_clear_auth_cookies"}
SESSION_COOKIE_NAMES = {"access_token", "refresh_token"}
POLICY_FUNCTION = "_auth_cookie_secure"

# Directories that are not application code.
_SKIP_PARTS = {"tests", "alembic", "__pycache__", ".venv", "node_modules"}


def _backend_modules() -> list[Path]:
    return sorted(
        path
        for path in BACKEND_ROOT.rglob("*.py")
        if not _SKIP_PARTS & set(path.relative_to(BACKEND_ROOT).parts)
    )


def test_the_module_walk_finds_application_code():
    """Guard the guard: an empty walk would make everything below vacuous."""
    modules = _backend_modules()
    assert AUTH_ROUTES_SOURCE in modules
    assert len(modules) > 20, len(modules)


def _cookie_calls(tree: ast.AST) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"set_cookie", "delete_cookie"}
    ]


def _keyword(node: ast.Call, name: str):
    for kw in node.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _cookie_name(node: ast.Call):
    """The literal cookie name, from ``key=`` or the first positional arg."""
    value = _keyword(node, "key")
    if value is None and node.args:
        value = node.args[0]
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    return None


def _names_derived_from_policy(tree: ast.AST) -> set[str]:
    """Locals assigned directly from ``_auth_cookie_secure(...)``."""
    derived = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        if not (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)):
            continue
        if value.func.id != POLICY_FUNCTION:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                derived.add(target.id)
    return derived


def test_every_session_cookie_derives_secure_from_the_policy_function():
    """Repo-wide: no session cookie may hardcode its own transport verdict.

    This is the guard that would have caught the original defect. It fails on a
    literal ``secure=False``, on an omitted ``secure=``, and on a value from any
    source other than ``_auth_cookie_secure`` — anywhere in the backend, not
    just in ``auth/routes.py``.
    """
    checked = []
    for path in _backend_modules():
        tree = ast.parse(path.read_text())
        derived = _names_derived_from_policy(tree)
        for node in _cookie_calls(tree):
            name = _cookie_name(node)
            if name not in SESSION_COOKIE_NAMES:
                continue
            where = f"{path.relative_to(BACKEND_ROOT)}:{node.lineno} ({name})"
            checked.append(where)

            secure = _keyword(node, "secure")
            assert secure is not None, f"{where} does not pass secure="
            if isinstance(secure, ast.Call) and isinstance(secure.func, ast.Name):
                assert secure.func.id == POLICY_FUNCTION, where
            elif isinstance(secure, ast.Name):
                assert secure.id in derived, (
                    f"{where} passes secure={secure.id}, which is not assigned "
                    f"from {POLICY_FUNCTION}() in this module"
                )
            else:
                raise AssertionError(
                    f"{where} passes a hardcoded secure= value; it must derive "
                    f"from {POLICY_FUNCTION}(request)"
                )

    assert checked, "found no session-cookie call sites at all"


def test_every_session_cookie_is_httponly_and_samesite_lax():
    for path in _backend_modules():
        tree = ast.parse(path.read_text())
        for node in _cookie_calls(tree):
            if _cookie_name(node) not in SESSION_COOKIE_NAMES:
                continue
            where = f"{path.relative_to(BACKEND_ROOT)}:{node.lineno}"
            httponly = _keyword(node, "httponly")
            samesite = _keyword(node, "samesite")
            assert isinstance(httponly, ast.Constant) and httponly.value is True, where
            assert isinstance(samesite, ast.Constant) and samesite.value == "lax", where


def test_every_cookie_helper_call_site_passes_the_request():
    """No call site may omit the transport context.

    The helpers take ``request`` with no default (pinned below) so an omission
    raises, but a caller could still pass ``None`` or a literal. This pins the
    argument to the name ``request``.
    """
    call_sites = []
    for path in _backend_modules():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            if node.func.id not in COOKIE_HELPERS:
                continue
            where = f"{path.relative_to(BACKEND_ROOT)}:{node.lineno} ({node.func.id})"
            call_sites.append(where)
            supplied = [
                arg for arg in node.args
                if isinstance(arg, ast.Name) and arg.id == "request"
            ] + [
                kw.value for kw in node.keywords
                if kw.arg == "request"
                and isinstance(kw.value, ast.Name)
                and kw.value.id == "request"
            ]
            assert supplied, f"{where} does not pass the request"

    assert call_sites, "found no cookie-helper call sites at all"


@pytest.mark.parametrize("helper", sorted(COOKIE_HELPERS))
def test_cookie_helpers_have_no_default_request(helper):
    """A defaulted ``request`` would let a new call site silently opt out."""
    tree = ast.parse(AUTH_ROUTES_SOURCE.read_text())
    definitions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == helper
    ]
    assert len(definitions) == 1, helper
    node = definitions[0]

    names = [arg.arg for arg in node.args.args]
    assert "request" in names, helper
    # Defaults bind to the tail of args; request must not be in it.
    defaulted = names[len(names) - len(node.args.defaults):] if node.args.defaults else []
    assert "request" not in defaulted, helper
