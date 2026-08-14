"""No 422 anywhere in the app may quote a request-body value.

Bead ``enhancedchannelmanager-9kwzp``, from a Codex pre-merge review of the
auth-posture branch.

WHY THIS FILE EXISTS AT ALL. Bead ``enhancedchannelmanager-2owpi`` fixed the
``RequestValidationError`` handler in ``main.py`` for two path prefixes,
``/api/auth`` and ``/api/tls``, and pinned that fix with a test that read the
prefix tuple and asserted the two prefixes were in it. That test passed on
every commit while sixteen other credential-bearing route/model pairs went on
writing operator passwords, Dispatcharr and Emby API keys, SMTP passwords,
Discord webhook URLs, Telegram bot tokens, Plex tokens, AWS keys and arbitrary
cloud-target ``credentials`` mappings into the application log at ERROR and
echoing them back to the caller in the 422 body.

Asserting on the shape of the mechanism is not asserting on the behaviour the
mechanism exists to produce. That is the specific failure this file is built
not to repeat, so:

* ``TestCredentialBearingRouteInventory`` derives the list of routes at risk
  from the LIVE app rather than from anyone's memory, and refuses to pass on an
  empty or shrunken walk (a silent zero-result sweep is the classic way an
  inventory test reports success while checking nothing).
* ``TestEveryCredentialBearingRouteIsRedacted`` runs the production handler
  against a REAL pydantic validation failure of each of those models, with
  placeholder credentials in the body, and asserts none of them reaches the
  response or the log.
* ``TestDiagnosticsSurviveOnOrdinaryRoutes`` proves the control did not buy
  its safety by making 422s useless: an ordinary route's 422 still names the
  offending field and says what was wrong with it.

No test in this file contains a real credential. Every placeholder follows
``docs/pytest_conventions.md`` -> "Credential Fixtures in Security Tests":
values begin with ``<``, so detect-secrets' ``SECRET`` regex never treats them
as scan candidates.
"""
import logging
from typing import Any

import pytest
from fastapi.routing import APIRoute
from pydantic import BaseModel, ValidationError
from fastapi.exceptions import RequestValidationError
from starlette.requests import Request

import main
from main import validation_exception_handler


# Placeholder credential value planted in every credential-shaped field. The
# suffix makes a leak attributable to this file if one ever shows up in a log.
PLACEHOLDER = "<synthetic-credential-value-9kwzp>"

# Substrings that mark a request-model field as credential-shaped. Deliberately
# broad and matched case-insensitively: this set decides what gets CHECKED, and
# a false positive here costs one extra assertion while a false negative costs
# a route nobody looks at. It is NOT the redaction control -- main.py redacts
# unconditionally and consults no list of names. See the design note above
# ``_BODY_REDACTED`` in main.py for why a name denylist was rejected as the
# control.
CREDENTIAL_FIELD_HINTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "credential",
    "access_key",
    "private_key",
    "webhook",
    "signing",
    "auth_key",
)

# Routes the Codex review named explicitly, plus the two the 2owpi prefix list
# already covered. If the walk below stops finding any of these, the walk is
# broken, not the app.
MUST_APPEAR = {
    "/api/settings",
    "/api/cloud-targets",
    "/api/sync-targets",
    "/api/admin/users",
    "/api/tls/configure",
    "/api/auth/login",
}

# Floor for the walk. The inventory stood at 25 route/model pairs when this
# file was written; routes get added and removed, so this is a floor rather
# than an equality, but a walk that suddenly finds five has broken.
MIN_INVENTORY_SIZE = 20


def _model_fields_recursive(
    model: type[BaseModel], seen: set | None = None, prefix: str = ""
) -> list[str]:
    """Every field name in a request model, including nested submodels.

    Nested models matter: a top-level scan would miss a credential carried one
    level down, which is the same class of miss this file is about.
    """
    if seen is None:
        seen = set()
    if model in seen:
        return []
    seen.add(model)

    names: list[str] = []
    for name, field in model.model_fields.items():
        names.append(prefix + name)
        annotation = field.annotation
        candidates = [annotation, *(getattr(annotation, "__args__", ()) or ())]
        for candidate in candidates:
            if isinstance(candidate, type) and issubclass(candidate, BaseModel):
                names.extend(
                    _model_fields_recursive(candidate, seen, prefix + name + ".")
                )
    return names


def _body_models(route: APIRoute) -> list[type[BaseModel]]:
    """The pydantic models a route parses its request body into."""
    models = []
    for param in route.dependant.body_params:
        annotation = getattr(param.field_info, "annotation", None)
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            models.append(annotation)
    return models


def credential_bearing_routes() -> list[tuple[str, str, type[BaseModel], list[str]]]:
    """Walk the LIVE app for body-taking routes whose models hold credentials.

    Returns ``(method, path, model, credential_field_names)`` tuples.
    """
    found = []
    for route in main.app.routes:
        if not isinstance(route, APIRoute):
            continue
        write_methods = sorted(route.methods & {"POST", "PUT", "PATCH"})
        if not write_methods:
            continue
        for model in _body_models(route):
            fields = _model_fields_recursive(model)
            hits = [
                f for f in fields
                if any(hint in f.lower() for hint in CREDENTIAL_FIELD_HINTS)
            ]
            if hits:
                found.append((write_methods[0], route.path, model, hits))
    return found


_INVENTORY = credential_bearing_routes()


def _leaf_name(dotted: str) -> str:
    return dotted.split(".")[-1]


def _real_validation_error(
    model: type[BaseModel], payload: dict[str, Any]
) -> ValidationError:
    """A genuine pydantic failure for ``model``, carrying ``payload`` as input.

    Two shots, both real pydantic, no hand-built error dicts:

    1. Validate the payload as-is. Most of these models have required fields
       the payload omits, so this raises ``missing`` errors whose ``input`` is
       the ENTIRE payload -- the realistic worst case, and the one that made
       redacting ``exc.body`` alone insufficient in bead 2owpi.
    2. If the model accepts the payload (every field optional), hand it the
       payload wrapped in a list. A model never validates from a list, so this
       always raises, and the offending ``input`` is again the payload.
    """
    try:
        model.model_validate(payload)
    except ValidationError as exc:
        return exc
    try:
        model.model_validate([payload])
    except ValidationError as exc:
        return exc
    raise AssertionError(
        f"{model.__name__} accepted both a bare payload and a list; this helper "
        "cannot produce a validation error for it and the test would be vacuous"
    )


def _request_for(path: str, method: str) -> Request:
    """A real starlette Request for ``path`` with an already-consumed body.

    The handler calls ``await request.body()``. Seeding ``_body`` is how
    starlette itself caches a consumed body, so the handler takes its normal
    path without a live receive channel.
    """
    request = Request({
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "scheme": "http",
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    })
    request._body = b"{}"
    return request


class TestCredentialBearingRouteInventory:
    """The walk itself has to be trustworthy before anything built on it is."""

    def test_walk_is_not_silently_empty(self):
        assert len(_INVENTORY) >= MIN_INVENTORY_SIZE, (
            "the route walk found "
            f"{len(_INVENTORY)} credential-bearing route/model pairs, below the "
            f"floor of {MIN_INVENTORY_SIZE}. Either the app lost most of its "
            "credential-taking routes, or _body_models/_model_fields_recursive "
            "stopped resolving them and every assertion built on this "
            "inventory has quietly become vacuous."
        )

    def test_walk_finds_every_route_the_review_named(self):
        paths = {path for _method, path, _model, _fields in _INVENTORY}
        for expected in MUST_APPEAR:
            assert any(p.startswith(expected) for p in paths), (
                f"{expected} carries credentials in its request body but the "
                "walk did not find it"
            )

    def test_inventory_is_wider_than_the_prefix_list_it_replaced(self):
        """The 2owpi prefix list covered /api/auth and /api/tls only.

        This is the finding restated as an assertion: the number of
        credential-bearing routes OUTSIDE those two prefixes is large, so a
        two-prefix allowlist was never going to be the whole control.
        """
        outside = [
            (m, p) for m, p, _model, _f in _INVENTORY
            if not p.startswith(("/api/auth", "/api/tls"))
        ]
        assert len(outside) >= 10, outside

    def test_no_path_allowlist_governs_the_redaction(self):
        """The failure mode of a new credential route must be "redacted".

        A path allowlist cannot give that, because its default for an unlisted
        path is to leak. If someone reintroduces one, this fails and points at
        the design note in main.py.
        """
        assert not hasattr(main, "CREDENTIAL_BEARING_BODY_PREFIXES"), (
            "main.CREDENTIAL_BEARING_BODY_PREFIXES is back. A hand-maintained "
            "allowlist of credential-bearing paths defaults to LEAKING for "
            "every path nobody remembered to add, which is how sixteen routes "
            "stayed exposed under the previous version. Redaction is "
            "unconditional; see the note above _BODY_REDACTED in main.py."
        )


class TestEveryCredentialBearingRouteIsRedacted:
    """The production handler, real pydantic errors, every route in the walk."""

    @pytest.mark.parametrize(
        "method,path,model,fields",
        _INVENTORY,
        ids=[f"{m}:{p}:{model.__name__}" for m, p, model, _f in _INVENTORY],
    )
    @pytest.mark.asyncio
    async def test_handler_redacts_the_body(
        self, method, path, model, fields, caplog
    ):
        payload = {_leaf_name(f): PLACEHOLDER for f in fields}
        pydantic_error = _real_validation_error(model, payload)

        # Sanity: the placeholder really is in the error we are about to feed
        # the handler. Without this the test could pass on an error that never
        # carried the value, which would make it vacuous.
        raw = str(pydantic_error.errors())
        assert PLACEHOLDER in raw, (
            f"{model.__name__} produced a validation error that does not carry "
            "the planted value, so this case proves nothing"
        )

        exc = RequestValidationError(pydantic_error.errors())
        exc.body = payload

        with caplog.at_level(logging.DEBUG):
            response = await validation_exception_handler(
                _request_for(path, method), exc
            )

        assert response.status_code == 422
        assert PLACEHOLDER not in response.body.decode(), (
            f"{method} {path} echoed a request-body credential in its 422"
        )
        logged = "\n".join(r.getMessage() for r in caplog.records)
        assert PLACEHOLDER not in logged, (
            f"{method} {path} wrote a request-body credential to the log"
        )


class TestDiagnosticsSurviveOnOrdinaryRoutes:
    """Redaction must not have been bought by making 422s uninformative."""

    # A public, non-credential route that requires one field. Sending the wrong
    # field produces a real 422 through the real middleware stack.
    ORDINARY_PATH = "/api/cron/validate"
    ORDINARY_MISSING_FIELD = "expression"

    @pytest.mark.asyncio
    async def test_422_still_names_the_field_and_the_problem(self, async_client):
        response = await async_client.post(
            self.ORDINARY_PATH, json={"not_the_field": "anything"},
        )

        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert detail, response.text

        # Which field: the caller can still tell what to fix.
        locs = [".".join(str(part) for part in err.get("loc", ())) for err in detail]
        assert any(self.ORDINARY_MISSING_FIELD in loc for loc in locs), locs

        # What was wrong with it, and of what class.
        for err in detail:
            assert err.get("msg"), err
            assert err.get("type"), err

    @pytest.mark.asyncio
    async def test_redaction_applies_to_ordinary_routes_too(
        self, async_client, caplog
    ):
        """The unconditional half of the claim, checked over real HTTP.

        ``/api/cron/validate`` holds no credentials and is on no list. Its body
        values are withheld anyway, which is exactly the property that makes a
        credential-bearing route added tomorrow safe without anyone acting.
        """
        with caplog.at_level(logging.DEBUG):
            response = await async_client.post(
                self.ORDINARY_PATH, json={"not_the_field": PLACEHOLDER},
            )

        assert response.status_code == 422, response.text
        assert PLACEHOLDER not in response.text
        logged = "\n".join(r.getMessage() for r in caplog.records)
        assert PLACEHOLDER not in logged

        # The response keeps its "body" key so its shape is unchanged, but the
        # value is the constant marker rather than the request body.
        assert response.json()["body"] == main._BODY_REDACTED
