"""bead enhancedchannelmanager-jy006 — what ``require_auth: false`` still refuses.

THE QUESTION THIS BEAD ANSWERED
-------------------------------

``require_auth: false`` is a real, supported ECM operating mode, not a bug
state. What that mode's blast radius SHOULD be had never been decided. In
practice it meant every ``Require*IfEnabled`` gate in ``auth/dependencies.py``
short-circuited to "anonymous is fine", so an anonymous LAN caller on such an
instance could replace the entire database, plant a persistent admin-equivalent
``mcp_api_key``, and install their own TLS private key.

THE PO'S DECISION (2026-08-13)
------------------------------

Gate exactly three IDENTITY PRIMITIVES even when ``require_auth`` is false.
Everything else stays open in that mode and is documented in
``docs/auth_middleware.md`` → "What ``require_auth: false`` permits".

    require_auth: false
      /api/settings, /api/channels, /api/streams, /api/journal -> open
      ---------------------------------------------------------------
      POST /api/backup/restore-initial      -> admin required
      POST/DELETE /api/settings/mcp-api-key -> admin required
      /api/tls certificate + key material   -> admin required

The line between the two halves is not "how destructive is it". POST
/api/settings and POST /api/backup/restore are both wide open in this mode and
both do serious damage. The line is DURABILITY OF THE RESULTING IDENTITY: each
of the three primitives leaves the caller holding a credential or a key that
keeps working after the operator turns authentication back on. A settings write
does not.

THE NO-IDENTITY CARVE-OUT
-------------------------

All three gates still serve an anonymous caller on an instance that holds NO
operator identity — no user row and ``setup_complete`` false. That is a genuine
first run, or a deliberately headless auth-disabled deployment. Without it,
"always require an admin" would make these routes permanently unreachable on
such an instance with no in-band recovery: the only way to obtain an admin
would be to run the setup wizard, which changes the posture the operator chose.
The shape is not invented here — it is the one already shipped and
security-reviewed under bead lf29s in ``routers.backup._guard_initial_restore``,
whose predicate is now the shared
``auth.dependencies.instance_has_operator_identity``.

WHAT THIS FILE COVERS
---------------------

Every branch of the new ``enforce_when_auth_disabled`` behaviour, against the
real routes rather than the dependency in isolation:

* auth ENABLED — unchanged (the pre-existing per-bead files still own the
  detailed non-admin / MCP cases; the case here is a regression tripwire).
* auth disabled, NO operator identity — allowed, by both identity signals.
* auth disabled, identity present, anonymous — refused.
* auth disabled, identity present, authenticated non-admin — refused.
* auth disabled, identity present, admin — allowed (proves it is a refusal of
  anonymity, not of the mode).
* auth disabled, identity present, MCP service principal — refused, and the
  403 still names the right surface.

``restore-initial``'s half of the same decision lives in
``tests/routers/test_backup.py::TestRestoreInitialIdentityGate`` because that
route is guarded in the handler, not by a dependency; the cross-check that both
halves read the same predicate is ``test_both_halves_share_one_predicate``
below.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config import DispatcharrSettings
from models import User

from .test_9kwzp11_tls_router_admin_gate import (
    TLS_MATERIAL_ROUTES,
    TLS_ROUTES as _ALL_TLS_ROUTES,
    _Gate,
    _route_id,
)


MCP_KEY = "mcp-service-principal-jy006"

MCP_API_KEY_PATH = "/api/settings/mcp-api-key"
LIFECYCLE_METHODS = ["POST", "DELETE"]


# ---------------------------------------------------------------------------
# Auth postures
# ---------------------------------------------------------------------------

def _auth(require_auth: bool, setup_complete: bool) -> MagicMock:
    stub = MagicMock()
    stub.require_auth = require_auth
    stub.setup_complete = setup_complete
    return stub


# Auth disabled with an identity established by ``setup_complete`` alone — the
# instance whose operator ran the wizard and then switched authentication off.
AUTH_OFF_OWNED = _auth(require_auth=False, setup_complete=True)

# Auth disabled with NO identity signal at all. Combined with an empty users
# table this is the headless / genuine-first-run instance the carve-out serves.
AUTH_OFF_UNOWNED = _auth(require_auth=False, setup_complete=False)

# Auth enabled — the unchanged baseline.
AUTH_ON = _auth(require_auth=True, setup_complete=True)


def _seed_operator(session) -> None:
    """Establish the OTHER identity signal: a durable user row.

    Deliberately paired with ``AUTH_OFF_UNOWNED`` in the tests that use it, so
    the row is the only thing making the instance owned. That proves the gate
    reads instance state and not just the auth-settings flag — which matters,
    because ``setup_complete`` is exactly the value bead qg14z showed can be
    false on an instance that does have an admin.
    """
    session.add(
        User(
            username="operator-jy006",
            email="operator-jy006@example.com",
            is_admin=True,
            is_active=True,
            auth_provider="local",
        )
    )
    session.commit()


def _non_admin() -> User:
    return User(
        id=6001,
        username="regular-user",
        is_admin=False,
        is_active=True,
        auth_provider="local",
    )


def _admin() -> User:
    return User(
        id=6002,
        username="admin-user",
        is_admin=True,
        is_active=True,
        auth_provider="local",
    )


def _mcp_runtime_settings() -> DispatcharrSettings:
    """Runtime settings whose ``mcp_api_key`` the auth layer will recognize."""
    return DispatcharrSettings(
        url="http://dispatcharr:8000",
        username="u",
        password="p",
        mcp_api_key=MCP_KEY,
    )


# ---------------------------------------------------------------------------
# Primitive 2 — POST / DELETE /api/settings/mcp-api-key
# ---------------------------------------------------------------------------

def _settings_router_mocks():
    """Patch the mcp-api-key handler's persistence, at its import site.

    ``save_settings`` is the write that mints or erases the credential, so
    asserting it was never called is the proof the gate answered BEFORE the
    handler ran — a 403 alone cannot tell a refusal apart from a handler that
    wrote and then failed.
    """
    stored = DispatcharrSettings(
        url="http://dispatcharr:8000",
        username="u",
        password="p",
        mcp_api_key="<stored-mcp-key-jy006>",
    )
    return (
        patch("routers.settings.get_settings", return_value=stored),
        patch("routers.settings.save_settings"),
        patch("routers.settings.clear_settings_cache"),
    )


class TestMcpApiKeyUnderDisabledAuth:
    """The credential-plant primitive."""

    @pytest.mark.parametrize("method", LIFECYCLE_METHODS)
    @pytest.mark.asyncio
    async def test_anonymous_refused_when_instance_is_owned(
        self, async_client, test_session, method
    ):
        """THE BEAD. Auth off, instance owned, no credentials — refused.

        This is the exact call that used to mint an anonymous caller a
        persistent admin-equivalent bearer credential on somebody's LAN.
        """
        get_mock, save_mock, cache_mock = _settings_router_mocks()
        with patch("auth.dependencies.get_auth_settings", return_value=AUTH_OFF_OWNED), \
             get_mock, save_mock as save_settings, cache_mock:
            response = await async_client.request(method, MCP_API_KEY_PATH)

        assert response.status_code == 401, response.json()
        save_settings.assert_not_called()

    @pytest.mark.parametrize("method", LIFECYCLE_METHODS)
    @pytest.mark.asyncio
    async def test_anonymous_refused_when_owned_by_a_user_row_alone(
        self, async_client, test_session, method
    ):
        """``setup_complete`` false but a user row exists — still refused.

        The qg14z state. If the gate keyed on ``setup_complete`` alone this
        would pass anonymously, which is the state in which the original live
        takeover was reproduced.
        """
        _seed_operator(test_session)
        get_mock, save_mock, cache_mock = _settings_router_mocks()
        with patch("auth.dependencies.get_auth_settings", return_value=AUTH_OFF_UNOWNED), \
             get_mock, save_mock as save_settings, cache_mock:
            response = await async_client.request(method, MCP_API_KEY_PATH)

        assert response.status_code == 401, response.json()
        save_settings.assert_not_called()

    @pytest.mark.parametrize("method", LIFECYCLE_METHODS)
    @pytest.mark.asyncio
    async def test_authenticated_non_admin_refused(
        self, async_client, test_session, method
    ):
        """Auth off is not a promotion: an ordinary signed-in user is still not
        an admin."""
        _seed_operator(test_session)
        get_mock, save_mock, cache_mock = _settings_router_mocks()
        with patch("auth.dependencies.get_auth_settings", return_value=AUTH_OFF_OWNED), \
             patch("auth.dependencies.get_current_user",
                   new=AsyncMock(return_value=_non_admin())), \
             get_mock, save_mock as save_settings, cache_mock:
            response = await async_client.request(method, MCP_API_KEY_PATH)

        assert response.status_code == 403, response.json()
        assert response.json()["detail"] == "Admin access required"
        save_settings.assert_not_called()

    @pytest.mark.parametrize("method", LIFECYCLE_METHODS)
    @pytest.mark.asyncio
    async def test_mcp_principal_still_refused(
        self, async_client, test_session, method
    ):
        """The auth-disabled branch must not lose the human-admin distinction.

        The MCP principal carries ``is_admin=True``, so a gate that merely
        required "an admin" once auth is off would ADMIT the bearer of the key
        to its own rotation — the 9kwzp.8 hole, reopened in a mode nobody
        tests. The 403 body must still name this surface.
        """
        _seed_operator(test_session)
        get_mock, save_mock, cache_mock = _settings_router_mocks()
        with patch("auth.dependencies.get_settings", return_value=_mcp_runtime_settings()), \
             patch("auth.dependencies.get_auth_settings", return_value=AUTH_OFF_OWNED), \
             get_mock, save_mock as save_settings, cache_mock:
            response = await async_client.request(
                method, MCP_API_KEY_PATH,
                headers={"Authorization": f"Bearer {MCP_KEY}"},
            )

        assert response.status_code == 403, response.json()
        detail = response.json()["detail"]
        assert "MCP service principal" in detail
        assert "MCP API key" in detail
        save_settings.assert_not_called()

    @pytest.mark.parametrize("method", LIFECYCLE_METHODS)
    @pytest.mark.asyncio
    async def test_admin_still_reaches_the_handler(
        self, async_client, test_session, method
    ):
        """Positive control: a refusal of anonymity, not of the mode.

        ``get_current_user`` carries no ``require_auth`` short-circuit, so an
        operator holding a session cookie authenticates normally on an
        auth-disabled instance. Without this case the refusals above could not
        be told apart from a hard lockout.
        """
        _seed_operator(test_session)
        get_mock, save_mock, cache_mock = _settings_router_mocks()
        with patch("auth.dependencies.get_auth_settings", return_value=AUTH_OFF_OWNED), \
             patch("auth.dependencies.get_current_user",
                   new=AsyncMock(return_value=_admin())), \
             get_mock, save_mock as save_settings, cache_mock:
            response = await async_client.request(method, MCP_API_KEY_PATH)

        assert response.status_code == 200, response.json()
        save_settings.assert_called_once()

    @pytest.mark.parametrize("method", LIFECYCLE_METHODS)
    @pytest.mark.asyncio
    async def test_unowned_instance_still_serves_anonymous(
        self, async_client, test_session, method
    ):
        """The carve-out: a headless auth-disabled instance configures its own
        sidecar."""
        get_mock, save_mock, cache_mock = _settings_router_mocks()
        with patch("auth.dependencies.get_auth_settings", return_value=AUTH_OFF_UNOWNED), \
             get_mock, save_mock as save_settings, cache_mock:
            response = await async_client.request(method, MCP_API_KEY_PATH)

        assert response.status_code == 200, response.json()
        save_settings.assert_called_once()

    @pytest.mark.parametrize("method", LIFECYCLE_METHODS)
    @pytest.mark.asyncio
    async def test_auth_enabled_admin_is_unchanged(
        self, async_client, test_session, method
    ):
        """Regression tripwire: the auth-ENABLED path is untouched by this bead."""
        get_mock, save_mock, cache_mock = _settings_router_mocks()
        with patch("auth.dependencies.get_auth_settings", return_value=AUTH_ON), \
             patch("auth.dependencies.get_current_user",
                   new=AsyncMock(return_value=_admin())), \
             get_mock, save_mock as save_settings, cache_mock:
            response = await async_client.request(method, MCP_API_KEY_PATH)

        assert response.status_code == 200, response.json()
        save_settings.assert_called_once()


# ---------------------------------------------------------------------------
# Primitive 3 — the /api/tls certificate and key material
# ---------------------------------------------------------------------------
#
# Applied to the WHOLE ``RequireHumanAdminForTLSMaterial`` set — all ten routes,
# including ``GET /settings``, ``DELETE /certificate`` and the https trio — not
# to the key-install subset alone. Reasons, in the order they carried weight:
#
#  1. It costs the operator nothing. ``TLSSettingsSection.tsx`` makes NO API
#     call at all when it renders non-admin (its ``useEffect`` returns at
#     ``if (!isAdmin) return``), and an auth-disabled instance is already in
#     that state — ``useAuth`` never resolves a user when ``require_auth`` is
#     false, so the section is handed ``isAdmin={user?.is_admin ?? false}``.
#     Nothing that works today stops working.
#  2. Splitting the set needs a SECOND gate constant with its own 403 body and
#     its own inventory group, which
#     ``tests/test_admin_gate_inventory.py`` explicitly argues against under
#     "WHERE THIS INVENTORY IS DELIBERATELY COARSER THAN ITS OWN PROSE": add
#     field- or route-level branching only when a concrete caller needs it.
#  3. Coarse fails in the safe direction.
#
# The two status reads (``GET /status``, ``GET /https/status``) keep plain
# ``RequireAdminIfEnabled`` and therefore stay OPEN in this mode; they disclose
# no credential material. ``POST /test-dns-provider`` also stays open, on
# ``RequireHumanAdminForOutboundTest`` with its eleven siblings — pinned by
# ``test_outbound_test_and_status_reads_stay_open`` below, because that is the
# sharpest residual of the PO's line and must not be discovered by accident.

class TestTLSMaterialUnderDisabledAuth:
    """The key-install primitive."""

    @pytest.mark.parametrize("route", TLS_MATERIAL_ROUTES, ids=_route_id)
    @pytest.mark.asyncio
    async def test_anonymous_refused_when_instance_is_owned(
        self, async_client, test_session, route
    ):
        with _Gate(async_client, route) as gate, \
                patch("auth.dependencies.get_auth_settings", return_value=AUTH_OFF_OWNED):
            response = await gate.request()

            assert response.status_code == 401, response.text
            gate.witness.assert_not_called()

    @pytest.mark.parametrize("route", TLS_MATERIAL_ROUTES, ids=_route_id)
    @pytest.mark.asyncio
    async def test_anonymous_refused_when_owned_by_a_user_row_alone(
        self, async_client, test_session, route
    ):
        _seed_operator(test_session)
        with _Gate(async_client, route) as gate, \
                patch("auth.dependencies.get_auth_settings", return_value=AUTH_OFF_UNOWNED):
            response = await gate.request()

            assert response.status_code == 401, response.text
            gate.witness.assert_not_called()

    @pytest.mark.parametrize("route", TLS_MATERIAL_ROUTES, ids=_route_id)
    @pytest.mark.asyncio
    async def test_authenticated_non_admin_refused(
        self, async_client, test_session, route
    ):
        _seed_operator(test_session)
        with _Gate(async_client, route) as gate, \
                patch("auth.dependencies.get_auth_settings", return_value=AUTH_OFF_OWNED), \
                patch("auth.dependencies.get_current_user",
                      new=AsyncMock(return_value=_non_admin())):
            response = await gate.request()

            assert response.status_code == 403, response.text
            assert response.json()["detail"] == "Admin access required"
            gate.witness.assert_not_called()

    @pytest.mark.parametrize("route", TLS_MATERIAL_ROUTES, ids=_route_id)
    @pytest.mark.asyncio
    async def test_mcp_principal_still_refused(
        self, async_client, test_session, route
    ):
        """The 403 must still name TLS, not a backup restore or a key rotation."""
        _seed_operator(test_session)
        with _Gate(async_client, route) as gate, \
                patch("auth.dependencies.get_settings",
                      return_value=_mcp_runtime_settings()), \
                patch("auth.dependencies.get_auth_settings", return_value=AUTH_OFF_OWNED):
            response = await gate.request(
                headers={"Authorization": f"Bearer {MCP_KEY}"}
            )

            assert response.status_code == 403, response.text
            detail = response.json()["detail"]
            assert "MCP service principal" in detail
            assert "TLS" in detail
            gate.witness.assert_not_called()

    @pytest.mark.parametrize("route", TLS_MATERIAL_ROUTES, ids=_route_id)
    @pytest.mark.asyncio
    async def test_admin_still_reaches_the_handler(
        self, async_client, test_session, route
    ):
        _seed_operator(test_session)
        with _Gate(async_client, route) as gate, \
                patch("auth.dependencies.get_auth_settings", return_value=AUTH_OFF_OWNED), \
                patch("auth.dependencies.get_current_user",
                      new=AsyncMock(return_value=_admin())):
            response = await gate.request()

            assert response.status_code == 200, response.text
            gate.witness.assert_called()

    @pytest.mark.parametrize("route", TLS_MATERIAL_ROUTES, ids=_route_id)
    @pytest.mark.asyncio
    async def test_unowned_instance_still_serves_anonymous(
        self, async_client, test_session, route
    ):
        """The carve-out, per route: a first-run instance still installs TLS."""
        with _Gate(async_client, route) as gate, \
                patch("auth.dependencies.get_auth_settings", return_value=AUTH_OFF_UNOWNED):
            response = await gate.request()

            assert response.status_code == 200, response.text
            gate.witness.assert_called()


# ---------------------------------------------------------------------------
# The other side of the PO's line — what stays OPEN, pinned deliberately
# ---------------------------------------------------------------------------

class TestEverythingElseStaysOpen:
    """"Everything else stays open in that mode" is half the decision.

    Pinned rather than left implicit, because an over-broad follow-up that
    quietly gated more of the surface would otherwise ship green, and because
    an operator reading the docs needs these to be facts about the build rather
    than intentions.
    """

    @pytest.mark.asyncio
    async def test_settings_read_stays_open(self, async_client, test_session):
        """GET /api/settings — the route this bead was originally filed about.

        It stays anonymous on an owned auth-disabled instance BY DECISION. What
        it discloses is bounded by bead 9ej7f's redaction, not by this gate.
        """
        _seed_operator(test_session)
        with patch("auth.dependencies.get_auth_settings", return_value=AUTH_OFF_OWNED), \
             patch("routers.settings.get_auth_settings", return_value=AUTH_OFF_OWNED):
            response = await async_client.get("/api/settings")

        assert response.status_code == 200, response.text

    @pytest.mark.parametrize(
        "path", ["/api/tls/status", "/api/tls/https/status"]
    )
    @pytest.mark.asyncio
    async def test_tls_status_reads_stay_open(
        self, async_client, test_session, path
    ):
        """The two plain-gated TLS reads disclose no credential material.

        They are the boundary of the wholesale enforcement applied to
        ``RequireHumanAdminForTLSMaterial``: if one of these starts refusing,
        the gate was widened past what the PO decided.
        """
        _seed_operator(test_session)
        route = next(
            r for r in _ALL_TLS_ROUTES if r.path == path and r.method == "GET"
        )
        with _Gate(async_client, route) as gate, \
                patch("auth.dependencies.get_auth_settings", return_value=AUTH_OFF_OWNED):
            response = await gate.request()

            assert response.status_code == 200, response.text

    @pytest.mark.asyncio
    async def test_dns_provider_probe_stays_open(self, async_client, test_session):
        """``POST /api/tls/test-dns-provider`` stays open — the sharpest residual.

        It runs on ``RequireHumanAdminForOutboundTest`` with its eleven
        siblings, and the PO's decision names none of those, so it is
        unenforced in this mode. That leaves a documented inconsistency INSIDE
        one router: ``GET /api/tls/settings`` is refused to an anonymous caller
        on an owned auth-disabled instance while this route, which exercises
        the DNS-provider credentials that route discloses in masked form, is
        not. Recorded here and in ``docs/auth_middleware.md`` so it is a known
        residual rather than an oversight; revisit with the other eleven sinks,
        not on its own.
        """
        _seed_operator(test_session)
        route = next(
            r for r in _ALL_TLS_ROUTES if r.path == "/api/tls/test-dns-provider"
        )
        with _Gate(async_client, route) as gate, \
                patch("auth.dependencies.get_auth_settings", return_value=AUTH_OFF_OWNED):
            response = await gate.request()

            assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
# One predicate, not two
# ---------------------------------------------------------------------------

def test_both_halves_share_one_predicate():
    """``routers.backup`` and ``auth.dependencies`` must not drift apart.

    The restore-initial guard and the ``enforce_when_auth_disabled`` branch ask
    the same question — "is this instance owned?" — and must answer it
    identically, or the auth-disabled posture is inconsistent between the
    restore path and the credential paths. ``routers.backup`` held a private
    copy until this bead. Two copies of one fail-closed security predicate is
    the drift defect bead 9kwzp.9 is about, and a copy would not fail any
    behavioural test until the day the two answers diverged.
    """
    import auth.dependencies as deps
    import routers.backup as backup

    assert backup.instance_has_operator_identity is deps.instance_has_operator_identity
    assert not hasattr(backup, "_instance_has_operator_identity")


def test_operator_identity_predicate_fails_closed():
    """An unreadable users table means OWNED, never "open season".

    The predicate decides whether an ANONYMOUS caller may proceed, so the
    unknown case must be the restrictive one. Asserted directly because no
    route test can reach it: it needs the database read to raise.
    """
    from auth.dependencies import instance_has_operator_identity

    exploding = MagicMock()
    exploding.query.side_effect = RuntimeError("users table unreadable")

    assert instance_has_operator_identity(exploding) is True


def test_only_the_decided_gates_enforce_when_auth_is_disabled():
    """Exactly two dependency gates carry ``enforce_when_auth_disabled``.

    The third primitive (restore-initial) is guarded in its handler, so it
    cannot appear here. Anything ELSE appearing here means the PO's line moved
    without the PO — every other gate in the family is supposed to keep
    no-opping while ``require_auth`` is false, and a widened gate would show up
    as an unexplained 401 on a supported configuration rather than as a test
    failure anywhere else.
    """
    import auth.dependencies as deps

    enforcing = set()
    for name in dir(deps):
        dependency = getattr(deps, name)
        call = getattr(dependency, "dependency", None)
        if getattr(call, "__qualname__", "") != "require_admin_if_enabled.<locals>.check_admin":
            continue
        captured = dict(
            zip(
                call.__code__.co_freevars,
                (cell.cell_contents for cell in call.__closure__ or ()),
            )
        )
        if captured.get("enforce_when_auth_disabled"):
            enforcing.add(name)

    assert enforcing == {
        "RequireHumanAdminForServiceCredential",
        "RequireHumanAdminForTLSMaterial",
    }, sorted(enforcing)
