"""bead enhancedchannelmanager-ne2yy — ``AUTH_EXEMPT_PATHS`` pinned exactly.

WHY THIS FILE EXISTS
--------------------

``main.AUTH_EXEMPT_PATHS`` is the entire authentication gate for ``/api/*``.
``main.auth_middleware`` enforces on every ``/api/`` path EXCEPT the members of
this set, and it is a membership test on the literal
``request.url.path`` — there is no prefix logic, no router opt-in, and no
second layer behind it for the routers that carry no route dependency of their
own. Adding one line to that set makes one route anonymous to the whole
internet-facing surface of the app.

Before this file, the gate was proved by exactly one test
(``tests/routers/test_client_errors.py::test_missing_jwt_returns_401_when_auth_enabled``),
which asserts that ONE non-exempt path returns 401. Nothing asserted the
CONTENTS of the set. So adding a data route to it was a one-line, silent,
total-exposure change that shipped with green CI. That is not hypothetical:
``/api/backup/restore-initial`` — a full ``journal.db`` replacement, admin
password hashes included — sat in this set until bead
enhancedchannelmanager-lf29s removed it.

This is the project's "enforcement code tests itself" rule applied to the
exempt list: the snapshot below is the reviewable artifact. Changing the set
now requires editing this file in the same commit, which puts the change in
front of a reviewer with the checklist in ``_REVIEW_CHECKLIST`` attached to it.

WHAT THIS FILE DOES NOT CLAIM
-----------------------------

* It does not assert that the exempt paths are SAFE to be public. It asserts
  that the set is what was last reviewed. Each entry's justification is the
  inline comment beside it in ``main.py``; this file pins membership, not
  merit.
* It does not assert that non-exempt paths are protected in every mode. The
  middleware only enforces while ``require_auth`` AND ``setup_complete`` are
  both true — ``docs/auth_middleware.md`` documents what ``require_auth:
  false`` permits and the three identity primitives that stay gated even then
  (bead jy006).
* Exemption from the MIDDLEWARE is not the same as being anonymous. Two
  members of this set carry their own always-enforcing route dependency:
  ``/api/auth/admin/settings`` (both verbs) uses ``auth.routes.require_admin``,
  which chains ``get_current_user`` and therefore validates a token regardless
  of ``require_auth``. ``test_exempt_paths_that_carry_their_own_dependency``
  pins that, because reading the set alone would overstate the exposure.
"""
import pytest

from main import AUTH_EXEMPT_PATHS


# ---------------------------------------------------------------------------
# The snapshot
# ---------------------------------------------------------------------------

# Grouped exactly as ``main.py`` groups them, so a diff here reads the same way
# as a diff there. Read the inline comments in ``main.py`` for each entry's
# justification — they are the record of WHY, and are deliberately not
# duplicated into this file where they would drift.
_HEALTH_AND_IDENTITY = {
    "/api/health",
    "/api/health/ready",
    "/api/health/schema",
    "/api/version",
}

# SLO-6 denominator ingest. Public by design (bd-m3vej): accepts one opaque
# UUIDv4, never logged, never persisted.
_TELEMETRY_INGEST = {
    "/api/session-start",
}

# The auth flow itself, which cannot require prior authentication without
# being unusable. Note ``/api/auth/admin/settings`` is NOT anonymous despite
# being here — see the module docstring and
# ``test_exempt_paths_that_carry_their_own_dependency``.
_AUTH_FLOW = {
    "/api/auth/login",
    "/api/auth/refresh",
    "/api/auth/logout",
    "/api/auth/status",
    "/api/auth/setup-required",
    "/api/auth/setup",
    "/api/auth/forgot-password",
    "/api/auth/reset-password",
    "/api/auth/providers",
    "/api/auth/dispatcharr/login",
    "/api/auth/admin/settings",
}

# OpenAPI documentation surfaces.
_API_DOCS = {
    "/api/docs",
    "/api/redoc",
    "/api/openapi.json",
}

EXPECTED_AUTH_EXEMPT_PATHS = frozenset(
    _HEALTH_AND_IDENTITY | _TELEMETRY_INGEST | _AUTH_FLOW | _API_DOCS
)


# The message a reviewer sees when this test fails. It is deliberately long:
# the failure output IS the review checklist, and the person reading it is
# usually the person who just added the line, mid-change, not someone who came
# here to read a doc.
_REVIEW_CHECKLIST = """
AUTH_EXEMPT_PATHS CHANGED. This set is the ENTIRE authentication gate for
/api/*: main.auth_middleware enforces on every /api/ path except an exact
string match against this set. A path added here is reachable by any
unauthenticated caller that can open a socket to ECM — there is no second
layer behind it unless that specific route carries its own dependency.

This test exists (bead enhancedchannelmanager-ne2yy) because adding a line to
that set was otherwise a silent, one-line, total-exposure change that shipped
green. /api/backup/restore-initial — a full journal.db replacement including
every admin password hash — was in this set until bead
enhancedchannelmanager-lf29s took it out.

DO NOT just paste the new set in to make this pass. For each ADDED path:

  1. What does the route return or do to an anonymous caller on the public
     internet? Answer for the response body, not just the status code.
  2. Does it read or write any user, credential, setting or file?
     If yes, it almost certainly does not belong here.
  3. Could it be made to work with a token instead? Exemption is for paths
     that CANNOT authenticate (the login flow, health probes for an external
     load balancer), not for paths that are merely inconvenient to.
  4. Is it rate-limited or bounded? An exempt POST is an unauthenticated write
     path.
  5. Does the route carry its own auth dependency that still enforces?
     If it does, say so in a comment beside it in main.py, the way
     /api/auth/admin/settings is covered here — otherwise the next reader
     will read the bare set and mis-assess the exposure.

For each REMOVED path: confirm the callers that relied on it now send
credentials — a removal breaks anonymous clients (load balancers, the setup
wizard, the SPA before login) rather than exposing anything.

Then update EXPECTED_AUTH_EXEMPT_PATHS in this file, in the same commit, with
the reasoning in the commit message and an inline comment beside the entry in
main.py.
"""


def test_auth_exempt_paths_matches_the_reviewed_snapshot():
    """The exempt set is exactly what was last reviewed — no more, no less."""
    added = AUTH_EXEMPT_PATHS - EXPECTED_AUTH_EXEMPT_PATHS
    removed = EXPECTED_AUTH_EXEMPT_PATHS - AUTH_EXEMPT_PATHS
    assert AUTH_EXEMPT_PATHS == EXPECTED_AUTH_EXEMPT_PATHS, (
        f"{_REVIEW_CHECKLIST}\n"
        f"NEWLY EXEMPT (now anonymous): {sorted(added)}\n"
        f"NO LONGER EXEMPT (now requires a token): {sorted(removed)}\n"
    )


def test_every_exempt_path_is_an_api_path():
    """A non-``/api/`` entry would be dead weight that reads as a grant.

    ``auth_middleware`` only consults this set for paths starting ``/api/``, so
    an entry outside that prefix can never match. It would exempt nothing while
    documenting an intent to exempt something — the worst combination for the
    next reader, who would count it as a reviewed grant.
    """
    non_api = {path for path in AUTH_EXEMPT_PATHS if not path.startswith("/api/")}
    assert non_api == set(), (
        "These entries can never match — auth_middleware only consults "
        f"AUTH_EXEMPT_PATHS for paths under /api/: {sorted(non_api)}"
    )


def test_no_exempt_path_carries_a_trailing_slash_or_query():
    """Membership is an exact match on ``request.url.path``.

    A trailing slash, a query string or a case variant silently exempts
    nothing, for the same reason as above. Pinned rather than assumed because
    the failure mode is invisible: the app keeps working, the path keeps
    returning 401, and the set still LOOKS like it grants access.
    """
    malformed = {
        path
        for path in AUTH_EXEMPT_PATHS
        if path != path.rstrip("/") or "?" in path or path != path.lower()
    }
    assert malformed == set(), (
        "Exempt entries must be the exact lowercase path with no trailing "
        f"slash and no query string: {sorted(malformed)}"
    )


@pytest.mark.parametrize("path", sorted(EXPECTED_AUTH_EXEMPT_PATHS))
def test_snapshot_entries_are_named_one_at_a_time(path):
    """Restate the snapshot per path, so a removal reads as a route name.

    ``test_auth_exempt_paths_matches_the_reviewed_snapshot`` reports a set
    diff; this reports ``test_...[/api/auth/login]`` in the failure list, which
    is what a reviewer scanning CI output actually recognizes.
    """
    assert path in AUTH_EXEMPT_PATHS, (
        f"{path} was exempt when this snapshot was last reviewed and is not "
        "any more. Callers that relied on reaching it anonymously now need a "
        "token."
    )


# ---------------------------------------------------------------------------
# The set is not the whole story — pin the part that reading it would miss
# ---------------------------------------------------------------------------

def test_exempt_paths_that_carry_their_own_dependency():
    """``/api/auth/admin/settings`` is middleware-exempt but NOT anonymous.

    Both verbs depend on ``auth.routes.require_admin``, which chains
    ``get_current_user`` — a dependency with no ``require_auth`` short-circuit
    of any kind, so it validates a token in every mode including auth-disabled.

    Pinned because the exempt set read on its own overstates the exposure here,
    and because the protection is one ``Depends`` away from being deleted by
    someone who reads the exemption and concludes the route is meant to be
    public. If this ever fails, the auth-settings surface — the route that
    turns ``require_auth`` on and off — just became anonymous.
    """
    from fastapi.routing import APIRoute

    from auth.dependencies import get_current_user
    from main import app

    def _chains_get_current_user(dependant) -> bool:
        if dependant.call is get_current_user:
            return True
        return any(_chains_get_current_user(sub) for sub in dependant.dependencies)

    guarded = {
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods - {"HEAD", "OPTIONS"}
        if route.path in AUTH_EXEMPT_PATHS and _chains_get_current_user(route.dependant)
    }

    assert guarded == {
        ("GET", "/api/auth/admin/settings"),
        ("PUT", "/api/auth/admin/settings"),
    }, sorted(guarded)
