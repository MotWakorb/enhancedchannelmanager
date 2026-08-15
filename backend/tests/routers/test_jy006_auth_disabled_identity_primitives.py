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

THE FOLLOW-UP DECISION (2026-08-15, bead 2u4e0)
-----------------------------------------------

The twelve connection-test routes on ``RequireHumanAdminForOutboundTest`` join
them, on a SECOND axis:

    POST /api/settings/test, /test-smtp, /test-discord, /test-telegram
    POST /api/settings/{emby,plex,jellyfin}/test-connection
    POST /api/alert-methods/{id}/test
    POST /api/m3u/digest/test
    POST /api/cloud-targets/test, /api/cloud-targets/{id}/test
    POST /api/tls/test-dns-provider                       -> admin required

That axis is CREDENTIAL ORACLE, not durability: each route reaches the network
with credentials ALREADY STORED on the instance, to a host the caller can often
name, and echoes the upstream verdict back. The caller spends a secret they
never had to learn and reads an in-band port scan off the reply. jy006 left the
family open because the decision it implemented named none of these, and the
residual it produced was incoherent inside one router — ``POST
/api/tls/test-dns-provider`` was drivable anonymously on an owned auth-disabled
instance while ``GET /api/tls/settings``, which discloses the same
DNS-provider credentials only MASKED, was refused. ``TestOutboundTestFamily``
below now pins the refusal that ``TestEverythingElseStaysOpen`` used to pin as
an open residual.

The operator cost was shown to the PO and accepted: on an auth-disabled
instance that HAS a user account, a browser that is not signed in gets 403 from
every Test Connection button in Settings. Signing in at ``/login`` (bead p388h)
restores them without touching ``require_auth``, and the no-identity carve-out
leaves the headless posture alone.

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
# no credential material. ``POST /test-dns-provider`` used to stay open with
# them, on ``RequireHumanAdminForOutboundTest`` with its eleven siblings; bead
# 2u4e0 closed that whole family on 2026-08-15, and it is now pinned as a
# REFUSAL by ``TestOutboundTestFamily`` below.

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


# ---------------------------------------------------------------------------
# The second axis — the outbound-test family (bead 2u4e0)
# ---------------------------------------------------------------------------

class TestOutboundTestFamily:
    """``RequireHumanAdminForOutboundTest``, closed on 2026-08-15.

    This class replaces ``TestEverythingElseStaysOpen::
    test_dns_provider_probe_stays_open``, which pinned the OPPOSITE outcome as
    a deliberate residual of the jy006 line. The residual is the reason this
    bead exists: it left one router self-contradictory, admitting an anonymous
    caller to the probe that SPENDS the stored DNS-provider credentials while
    refusing the read that discloses them masked.

    The behavioural case below drives the real route the residual named. The
    whole family is asserted structurally by
    ``test_the_outbound_test_family_is_enforced_wholesale``, because gating one
    member and leaving eleven open is precisely the shape the PO rejected.
    """

    @pytest.mark.asyncio
    async def test_dns_provider_probe_is_refused_when_the_instance_is_owned(
        self, async_client, test_session
    ):
        """RED BEFORE 2u4e0: this returned 200 to an anonymous caller."""
        _seed_operator(test_session)
        route = next(
            r for r in _ALL_TLS_ROUTES if r.path == "/api/tls/test-dns-provider"
        )
        with _Gate(async_client, route) as gate, \
                patch("auth.dependencies.get_auth_settings", return_value=AUTH_OFF_OWNED):
            response = await gate.request()

            assert response.status_code == 401, response.text
            # The probe never happened: a 401 alone cannot tell a refusal apart
            # from a handler that ran and then failed.
            gate.witness.assert_not_called()

    @pytest.mark.asyncio
    async def test_unowned_instance_still_reaches_the_probe(
        self, async_client, test_session
    ):
        """The carve-out holds: a headless instance still tests its own DNS."""
        route = next(
            r for r in _ALL_TLS_ROUTES if r.path == "/api/tls/test-dns-provider"
        )
        with _Gate(async_client, route) as gate, \
                patch("auth.dependencies.get_auth_settings", return_value=AUTH_OFF_UNOWNED):
            response = await gate.request()

            assert response.status_code == 200, response.text
            gate.witness.assert_called()

    @pytest.mark.asyncio
    async def test_admin_still_reaches_the_probe(self, async_client, test_session):
        """Positive control: a refusal of anonymity, not of the mode.

        Without this, the refusal above could not be told apart from an
        operator lockout, which is exactly the cost the PO weighed.
        """
        _seed_operator(test_session)
        route = next(
            r for r in _ALL_TLS_ROUTES if r.path == "/api/tls/test-dns-provider"
        )
        with _Gate(async_client, route) as gate, \
                patch("auth.dependencies.get_auth_settings", return_value=AUTH_OFF_OWNED), \
                patch("auth.dependencies.get_current_user",
                      new=AsyncMock(return_value=_admin())):
            response = await gate.request()

            assert response.status_code == 200, response.text
            gate.witness.assert_called()


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


_CHECK_ADMIN_QUALNAME = "require_admin_if_enabled.<locals>.check_admin"


def _enforces_when_auth_disabled(call) -> bool:
    """Read the flag one gate closure was built with, or False if not a gate."""
    if getattr(call, "__qualname__", "") != _CHECK_ADMIN_QUALNAME:
        return False
    captured = dict(
        zip(
            call.__code__.co_freevars,
            (cell.cell_contents for cell in call.__closure__ or ()),
        )
    )
    return bool(captured.get("enforce_when_auth_disabled"))


def test_only_the_decided_gates_enforce_when_auth_is_disabled():
    """Exactly three dependency gates carry ``enforce_when_auth_disabled``.

    Two under jy006 (durability of the resulting identity) and one under 2u4e0
    (credential oracle). The remaining jy006 primitive, restore-initial, is
    guarded in its handler, so it cannot appear here. Anything ELSE appearing
    here means the line moved without the PO — every other gate in the family
    is supposed to keep no-opping while ``require_auth`` is false, and a
    widened gate would show up as an unexplained 401 on a supported
    configuration rather than as a test failure anywhere else.
    """
    import auth.dependencies as deps

    enforcing = {
        name
        for name in dir(deps)
        if _enforces_when_auth_disabled(
            getattr(getattr(deps, name), "dependency", None)
        )
    }

    assert enforcing == {
        "RequireHumanAdminForServiceCredential",
        "RequireHumanAdminForTLSMaterial",
        "RequireHumanAdminForOutboundTest",
    }, sorted(enforcing)


# Every route the PO's two decisions cover, as the live app serves them. The
# gate-set assertion above cannot see this: a gate could carry the flag and be
# wired to eleven of its twelve routes, which is the partial-coverage shape
# bead 2u4e0 was filed to remove.
ENFORCED_WHEN_AUTH_DISABLED_ROUTES = {
    # jy006 — the service-credential primitive.
    ("POST", "/api/settings/mcp-api-key"),
    ("DELETE", "/api/settings/mcp-api-key"),
    # jy006 — the TLS certificate/key material and HTTPS lifecycle.
    ("GET", "/api/tls/settings"),
    ("POST", "/api/tls/configure"),
    ("POST", "/api/tls/request-cert"),
    ("POST", "/api/tls/complete-challenge"),
    ("POST", "/api/tls/upload-cert"),
    ("POST", "/api/tls/renew"),
    ("POST", "/api/tls/https/start"),
    ("POST", "/api/tls/https/stop"),
    ("POST", "/api/tls/https/restart"),
    ("DELETE", "/api/tls/certificate"),
    # 2u4e0 — the twelve credential-oracle connection tests.
    ("POST", "/api/settings/test"),
    ("POST", "/api/settings/test-smtp"),
    ("POST", "/api/settings/test-discord"),
    ("POST", "/api/settings/test-telegram"),
    ("POST", "/api/settings/emby/test-connection"),
    ("POST", "/api/settings/plex/test-connection"),
    ("POST", "/api/settings/jellyfin/test-connection"),
    ("POST", "/api/alert-methods/{method_id}/test"),
    ("POST", "/api/m3u/digest/test"),
    ("POST", "/api/cloud-targets/test"),
    ("POST", "/api/cloud-targets/{target_id}/test"),
    ("POST", "/api/tls/test-dns-provider"),
}


def _routes_enforced_when_auth_disabled() -> set:
    """Walk the live FastAPI dependency tree, as the inventory module does."""
    from fastapi.routing import APIRoute

    from main import app

    def walk(dependant) -> bool:
        if _enforces_when_auth_disabled(dependant.call):
            return True
        return any(walk(sub) for sub in dependant.dependencies)

    found = set()
    for route in app.routes:
        if isinstance(route, APIRoute) and walk(route.dependant):
            for method in route.methods - {"HEAD", "OPTIONS"}:
                found.add((method, route.path))
    return found


def test_the_outbound_test_family_is_enforced_wholesale():
    """All twelve sinks, or the decision was not implemented.

    Bead 2u4e0's scope was the FAMILY, not ``/test-dns-provider`` alone: gating
    the one route the residual happened to name would have left eleven equally
    credential-carrying probes open and produced the same incoherence one
    router over. A route dropping out of this set is that regression.
    """
    outbound_tests = {
        entry for entry in ENFORCED_WHEN_AUTH_DISABLED_ROUTES if "test" in entry[1]
    }
    assert len(outbound_tests) == 12, sorted(outbound_tests)
    assert outbound_tests <= _routes_enforced_when_auth_disabled()


def test_no_other_route_enforces_when_auth_is_disabled():
    """The other half: nothing joined the set without a decision.

    ``require_auth: false`` is a supported posture, so a route quietly gaining
    this enforcement is an operator-visible refusal on a configuration that
    used to work. It must be a deliberate edit to this list, and to
    ``docs/auth_middleware.md`` with it.
    """
    enforced = _routes_enforced_when_auth_disabled()

    assert enforced == ENFORCED_WHEN_AUTH_DISABLED_ROUTES, {
        "newly enforced": sorted(enforced - ENFORCED_WHEN_AUTH_DISABLED_ROUTES),
        "no longer enforced": sorted(ENFORCED_WHEN_AUTH_DISABLED_ROUTES - enforced),
    }
