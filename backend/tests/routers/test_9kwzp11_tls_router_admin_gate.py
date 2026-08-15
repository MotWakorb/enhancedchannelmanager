"""bead 9kwzp.11 — every /api/tls route must be admin-gated, and most must be
human-admin.

THE GAP
-------

``backend/tls/routes.py`` carried ZERO route-level auth dependencies across all
thirteen routes. ``grep -cE 'Require|Depends\\(get_current' backend/tls/routes.py``
returned 0. Everything rested on the global ``auth_middleware`` in ``main.py``,
which establishes only that the caller is authenticated — no ``/api/tls`` path
was ever in ``AUTH_EXEMPT_PATHS``, so authentication was always required and
authorization never was.

So any authenticated NON-ADMIN, and the static MCP service principal (which
``auth.dependencies._build_mcp_service_principal`` stamps ``is_admin=True`` and
which ``auth_middleware`` accepts across the whole ``/api/`` surface), could:

* upload attacker-supplied certificate material and have it served
  (``POST /upload-cert``), or destroy the operator's own material
  (``DELETE /certificate``);
* rewrite TLS configuration including the plaintext DNS-provider credentials
  in ``/config/tls_settings.json`` (``POST /configure``, and see bead 2owpi for
  how weakly those are protected at rest);
* exercise those stored credentials against the DNS provider
  (``POST /test-dns-provider``);
* drive ACME issuance and renewal (``POST /request-cert``,
  ``/complete-challenge``, ``/renew``);
* stop the operator's own HTTPS termination (``POST /https/stop``).

WHY A FOURTH DENIAL DETAIL
--------------------------

``RequireHumanAdminForOutboundTest`` fits ``POST /test-dns-provider`` exactly:
it hands DNS-provider credentials to the provider API and reports the upstream
verdict back, which is the credential-validity oracle the eleven sinks of
i4qrp / 9kwzp.6 / 9kwzp.7 were gated as. Its 403 body already says the right
thing there.

Nothing in the existing family fits the other nine. ``RequireHumanAdminIfEnabled``
says "cannot perform backup restore" and ``RequireHumanAdminForServiceCredential``
says "cannot manage the MCP API key" — a caller refused on ``/upload-cert``
being told either one sends incident triage at a surface this router never
touches. That is the same reasoning that made 9kwzp.8 add a third constant
rather than reuse the second, so this bead adds a fourth,
``RequireHumanAdminForTLSMaterial``, over the SAME
``require_admin_if_enabled`` factory. Behaviour is identical to its three
siblings; only the operator-facing 403 body differs.

WHY THE TWO READS STAY ON THE PLAIN ADMIN TIER
----------------------------------------------

``GET /status`` and ``GET /https/status`` disclose configuration and
infrastructure detail (domain, port, issuer, expiry, whether HTTPS is up) but
no credential material, and a certificate's subject/issuer/validity window is
information every TLS client is served anyway. They take
``RequireAdminIfEnabled``: the non-admin half closes, the automation credential
stays admitted, which is the inventory's stated default for everything that is
not one of the denied classes.

``GET /settings`` is deliberately NOT in that group. It returns
``dns_zone_id`` and ``acme_email`` in clear and the last four characters of
``dns_api_token``, ``aws_access_key_id`` and ``aws_secret_access_key``. That is
credential material, partial or not, and withholding stored credential values
from this principal is exactly the posture bead 9ej7f established on
GET /api/settings. So it joins the denied group.

WHAT THIS FILE DOES NOT TOUCH
-----------------------------

The masking in ``get_tls_settings_endpoint`` and the ``startswith("***")``
round-trip guard in ``configure_tls`` are correct and paired, and are the guard
``routers/settings.py`` lacked. This bead is authorization only; the redaction
behaviour is unchanged and untested here on purpose.
"""
from datetime import datetime, timedelta
from typing import Callable, Dict, NamedTuple

import pytest
from fastapi.routing import APIRoute
from unittest.mock import AsyncMock, MagicMock, patch

from config import DispatcharrSettings
from models import User
from tls.settings import TLSSettings


# The static MCP key the auth layer will recognize. Hyphenated words, not a
# credential-shaped literal: nothing here depends on its shape, only on the
# runtime settings and the Authorization header agreeing.
MCP_KEY = "mcp-service-principal-9kwzp11"

# Placeholder credentials for the request bodies. Angle-bracket form per
# docs/pytest_conventions.md -> "Credential Fixtures in Security Tests": the
# SECRET regex's ``(?=\w+)`` lookahead means a value opening with ``<`` is
# never a scan candidate, and nothing in these tests depends on their shape.
DNS_TOKEN_PLACEHOLDER = "<synthetic-dns-api-token-9kwzp11>"
CERT_PEM_PLACEHOLDER = b"<synthetic-certificate-pem-9kwzp11>"
KEY_PEM_PLACEHOLDER = b"<synthetic-private-key-pem-9kwzp11>"

DENIED = "denied"
ADMITTED = "admitted"


class _Route(NamedTuple):
    """One TLS route, its intended MCP verdict, and how to prove the handler ran.

    ``witness`` resolves, from the patched router mocks, the first call the
    handler makes that the gate must prevent. A refused request must leave it
    untouched; an admitted one must reach it. Asserting on the status code
    alone would not distinguish a gate that answered from a handler that
    happened to fail.
    """

    method: str
    path: str
    verdict: str
    witness: Callable[[Dict[str, MagicMock]], MagicMock]
    request_kwargs: dict


_CONFIGURE_BODY = {
    "enabled": True,
    "mode": "letsencrypt",
    "domain": "tls-gate.example.com",
    "https_port": 6143,
    "acme_email": "operator@example.com",
    "dns_provider": "cloudflare",
    "dns_api_token": DNS_TOKEN_PLACEHOLDER,
}

_DNS_TEST_BODY = {
    "provider": "cloudflare",
    "api_token": DNS_TOKEN_PLACEHOLDER,
    "zone_id": "<synthetic-zone-id>",
}

_UPLOAD_FILES = {
    "cert_file": ("cert.pem", CERT_PEM_PLACEHOLDER, "application/x-pem-file"),
    "key_file": ("key.pem", KEY_PEM_PLACEHOLDER, "application/x-pem-file"),
}


TLS_ROUTES = (
    _Route("GET", "/api/tls/status", ADMITTED,
           lambda m: m["get_tls_settings"], {}),
    _Route("GET", "/api/tls/https/status", ADMITTED,
           lambda m: m["https_server_manager"].get_status, {}),
    _Route("GET", "/api/tls/settings", DENIED,
           lambda m: m["get_tls_settings"], {}),
    _Route("POST", "/api/tls/configure", DENIED,
           lambda m: m["save_tls_settings"], {"json": _CONFIGURE_BODY}),
    _Route("POST", "/api/tls/request-cert", DENIED,
           lambda m: m["get_tls_settings"], {}),
    _Route("POST", "/api/tls/complete-challenge", DENIED,
           lambda m: m["get_tls_settings"], {}),
    _Route("POST", "/api/tls/upload-cert", DENIED,
           lambda m: m["CertificateStorage"], {"files": _UPLOAD_FILES}),
    _Route("POST", "/api/tls/renew", DENIED,
           lambda m: m["renew_certificate"], {}),
    _Route("POST", "/api/tls/https/start", DENIED,
           lambda m: m["get_tls_settings"], {}),
    _Route("POST", "/api/tls/https/stop", DENIED,
           lambda m: m["https_server_manager"].stop, {}),
    _Route("POST", "/api/tls/https/restart", DENIED,
           lambda m: m["https_server_manager"].restart, {}),
    _Route("DELETE", "/api/tls/certificate", DENIED,
           lambda m: m["CertificateStorage"].return_value.delete_certificate, {}),
    _Route("POST", "/api/tls/test-dns-provider", DENIED,
           lambda m: m["get_dns_provider"], {}),
)

# ``/test-dns-provider`` needs a body; declared here rather than inline above so
# the table stays readable.
TLS_ROUTES = tuple(
    r._replace(request_kwargs={"json": _DNS_TEST_BODY})
    if r.path == "/api/tls/test-dns-provider" else r
    for r in TLS_ROUTES
)

DENIED_ROUTES = tuple(r for r in TLS_ROUTES if r.verdict == DENIED)
ADMITTED_ROUTES = tuple(r for r in TLS_ROUTES if r.verdict == ADMITTED)

# ``/test-dns-provider`` is the one member of the denied group gated by
# ``RequireHumanAdminForOutboundTest``; the other nine take the new
# ``RequireHumanAdminForTLSMaterial``. The 403 bodies differ accordingly.
OUTBOUND_TEST_ROUTE = next(
    r for r in TLS_ROUTES if r.path == "/api/tls/test-dns-provider"
)
TLS_MATERIAL_ROUTES = tuple(r for r in DENIED_ROUTES if r is not OUTBOUND_TEST_ROUTE)


def _route_id(route: _Route) -> str:
    return f"{route.method} {route.path}"


def _tls_settings() -> TLSSettings:
    """Settings complete enough that every handler gets past its own 400s."""
    return TLSSettings(
        enabled=True,
        mode="letsencrypt",
        domain="tls-gate.example.com",
        acme_email="operator@example.com",
        https_port=6143,
    )


def _validation_result() -> MagicMock:
    validation = MagicMock()
    validation.is_valid = True
    validation.validation_error = None
    validation.not_before = datetime(2026, 1, 1)
    validation.not_after = datetime(2026, 1, 1) + timedelta(days=90)
    validation.subject = "tls-gate.example.com"
    validation.issuer = "Synthetic Test CA"
    validation.domains = ["tls-gate.example.com"]
    validation.days_until_expiry.return_value = 90
    return validation


def _renewal_result() -> MagicMock:
    result = MagicMock()
    result.success = True
    result.error = None
    result.expires_at = datetime(2026, 1, 1) + timedelta(days=90)
    return result


def _router_mocks():
    """Patch every side effect ``tls.routes`` reaches for, at its import site.

    Returns ``(context_managers, mocks)``. The mocks are pre-wired so that an
    ADMITTED caller reaches a successful handler on every route, which is what
    makes ``witness.assert_not_called()`` meaningful on a refused one: the call
    is absent because the gate answered, not because the handler errored out.
    """
    storage_cls = MagicMock()
    storage = storage_cls.return_value
    storage.has_certificate.return_value = True
    storage.get_certificate_info.return_value = None
    storage.validate_pair.return_value = _validation_result()
    storage.save_certificate.return_value = True
    storage.delete_certificate.return_value = True

    https_manager = MagicMock()
    https_manager.is_running = True
    https_manager.start = AsyncMock(return_value=(True, None))
    https_manager.stop = AsyncMock(return_value=None)
    https_manager.restart = AsyncMock(return_value=(True, None))
    https_manager.get_status.return_value = {"running": True, "port": 6143}

    acme_cls = MagicMock()
    # ``initialize`` False short-circuits both ACME handlers into a 200 with
    # ``success: False``. The gate, not the ACME outcome, is what is under test.
    acme_cls.return_value.initialize = AsyncMock(return_value=False)

    dns_provider = MagicMock()
    dns_provider.verify_credentials = AsyncMock(return_value=(True, None))
    get_dns_provider = MagicMock(return_value=dns_provider)

    mocks = {
        "get_tls_settings": MagicMock(side_effect=lambda: _tls_settings()),
        "save_tls_settings": MagicMock(),
        "CertificateStorage": storage_cls,
        "https_server_manager": https_manager,
        "ACMEClient": acme_cls,
        "get_dns_provider": get_dns_provider,
        "renew_certificate": AsyncMock(return_value=_renewal_result()),
    }

    contexts = [patch(f"tls.routes.{name}", new=mock) for name, mock in mocks.items()]
    # ACME and DNS-provider imports are conditional on josepy; force the
    # available branch so the handlers are reachable regardless of the
    # interpreter the suite runs on.
    contexts.append(patch("tls.routes._acme_available", new=True))
    return contexts, mocks


class _Gate:
    """Enter the router mocks plus one auth posture, and issue one request."""

    def __init__(self, async_client, route: _Route):
        self._client = async_client
        self._route = route
        self._contexts, self.mocks = _router_mocks()

    def __enter__(self):
        for ctx in self._contexts:
            ctx.__enter__()
        return self

    def __exit__(self, *exc):
        for ctx in reversed(self._contexts):
            ctx.__exit__(*exc)
        return False

    async def request(self, **kwargs):
        return await self._client.request(
            self._route.method, self._route.path,
            **self._route.request_kwargs, **kwargs,
        )

    @property
    def witness(self) -> MagicMock:
        return self._route.witness(self.mocks)


def _non_admin_user() -> User:
    return User(
        id=9111,
        username="regular-user",
        is_admin=False,
        is_active=True,
        auth_provider="local",
    )


def _admin_user() -> User:
    return User(
        id=9112,
        username="admin-user",
        is_admin=True,
        is_active=True,
        auth_provider="local",
    )


def _mcp_runtime_settings() -> DispatcharrSettings:
    return DispatcharrSettings(
        url="http://dispatcharr:8000",
        username="u",
        password="<synthetic-dispatcharr-password>",
        mcp_api_key=MCP_KEY,
    )


# ---------------------------------------------------------------------------
# Coverage guard — a new TLS route cannot slip in without a verdict
# ---------------------------------------------------------------------------
def test_every_tls_route_has_a_recorded_verdict():
    """The thirteen routes above are ALL of them.

    ``test_admin_gate_inventory`` pins routes that HAVE a gate; an ungated new
    route is invisible to it. This walks the live app so adding a fourteenth
    ``/api/tls`` route fails here until someone decides its tier.
    """
    from main import app

    live = {
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute) and route.path.startswith("/api/tls")
        for method in route.methods - {"HEAD", "OPTIONS"}
    }
    assert live == {(r.method, r.path) for r in TLS_ROUTES}


def test_no_tls_path_is_auth_exempt():
    """Authentication was never the missing half; authorization was.

    If a ``/api/tls`` path were ever added to ``AUTH_EXEMPT_PATHS`` the route
    dependencies below would still run, but the reasoning in this file (that
    every caller is at least authenticated) would stop holding.
    """
    from main import AUTH_EXEMPT_PATHS

    assert not [p for p in AUTH_EXEMPT_PATHS if p.startswith("/api/tls")]


# ---------------------------------------------------------------------------
# Case 1 — the authenticated non-admin is refused everywhere
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("route", TLS_ROUTES, ids=_route_id)
@pytest.mark.asyncio
async def test_non_admin_is_refused_on_every_tls_route(async_client, route):
    with _Gate(async_client, route) as gate, \
            patch("auth.dependencies.get_auth_settings") as auth_mock, \
            patch("auth.dependencies.get_current_user",
                  new=AsyncMock(return_value=_non_admin_user())):
        auth_mock.return_value.require_auth = True
        auth_mock.return_value.setup_complete = True
        response = await gate.request()

        assert response.status_code == 403, response.text
        assert response.json()["detail"] == "Admin access required"
        gate.witness.assert_not_called()


# ---------------------------------------------------------------------------
# Case 2 — the MCP service principal is refused on the lifecycle routes
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("route", DENIED_ROUTES, ids=_route_id)
@pytest.mark.asyncio
async def test_mcp_principal_is_refused_on_tls_lifecycle_routes(async_client, route):
    """``RequireAdminIfEnabled`` would ADMIT this principal.

    ``_build_mcp_service_principal`` sets ``is_admin=True``, so the ordinary
    admin tier closes only the non-admin half of the hole and leaves the
    automation credential able to replace or destroy certificate material.
    This is the case that separates a real fix from one that reads as fixed.
    """
    with _Gate(async_client, route) as gate, \
            patch("auth.dependencies.get_settings",
                  return_value=_mcp_runtime_settings()), \
            patch("auth.dependencies.get_auth_settings") as auth_mock:
        auth_mock.return_value.require_auth = True
        auth_mock.return_value.setup_complete = True
        response = await gate.request(
            headers={"Authorization": f"Bearer {MCP_KEY}"}
        )

        assert response.status_code == 403, response.text
        assert "MCP service principal" in response.json()["detail"]
        gate.witness.assert_not_called()


@pytest.mark.parametrize("route", TLS_MATERIAL_ROUTES, ids=_route_id)
@pytest.mark.asyncio
async def test_tls_material_denial_names_tls_and_no_neighbouring_surface(
    async_client, route
):
    """The 403 body must name THIS surface.

    Reusing a sibling dependency would leave the status code 403 and this file
    green while every triage of the refusal started at a backup restore, an MCP
    key rotation or a connection test that these routes never perform. That is
    exactly why ``mcp_denial_detail`` is a per-call-site parameter.
    """
    with _Gate(async_client, route) as gate, \
            patch("auth.dependencies.get_settings",
                  return_value=_mcp_runtime_settings()), \
            patch("auth.dependencies.get_auth_settings") as auth_mock:
        auth_mock.return_value.require_auth = True
        auth_mock.return_value.setup_complete = True
        response = await gate.request(
            headers={"Authorization": f"Bearer {MCP_KEY}"}
        )

    detail = response.json()["detail"]
    assert "TLS" in detail
    assert "backup restore" not in detail
    assert "MCP API key" not in detail
    assert "connection test" not in detail


@pytest.mark.asyncio
async def test_dns_provider_test_denial_names_the_connection_test(async_client):
    """``/test-dns-provider`` keeps the outbound-test wording, not the TLS one.

    It is the one route in this router that hands credentials to an upstream
    and reports the verdict back, which is the i4qrp class verbatim, so its
    refusal should read like the other eleven sinks rather than like a
    certificate-lifecycle refusal.
    """
    with _Gate(async_client, OUTBOUND_TEST_ROUTE) as gate, \
            patch("auth.dependencies.get_settings",
                  return_value=_mcp_runtime_settings()), \
            patch("auth.dependencies.get_auth_settings") as auth_mock:
        auth_mock.return_value.require_auth = True
        auth_mock.return_value.setup_complete = True
        response = await gate.request(
            headers={"Authorization": f"Bearer {MCP_KEY}"}
        )

    detail = response.json()["detail"]
    assert "connection test" in detail
    gate.witness.assert_not_called()


@pytest.mark.parametrize("route", ADMITTED_ROUTES, ids=_route_id)
@pytest.mark.asyncio
async def test_mcp_principal_still_reaches_the_read_only_status_routes(
    async_client, route
):
    """The two status reads stay on the plain admin tier.

    Neither returns credential material, and the inventory's default for
    anything outside the denied classes is that the automation credential is
    admitted. Pinning it here means demoting or promoting either read is a
    deliberate edit rather than a copied dependency.
    """
    with _Gate(async_client, route) as gate, \
            patch("auth.dependencies.get_settings",
                  return_value=_mcp_runtime_settings()), \
            patch("auth.dependencies.get_auth_settings") as auth_mock:
        auth_mock.return_value.require_auth = True
        auth_mock.return_value.setup_complete = True
        response = await gate.request(
            headers={"Authorization": f"Bearer {MCP_KEY}"}
        )

        assert response.status_code == 200, response.text
        gate.witness.assert_called()


# ---------------------------------------------------------------------------
# Case 3 — the human admin reaches every handler
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("route", TLS_ROUTES, ids=_route_id)
@pytest.mark.asyncio
async def test_admin_reaches_every_tls_handler(async_client, route):
    with _Gate(async_client, route) as gate, \
            patch("auth.dependencies.get_auth_settings") as auth_mock, \
            patch("auth.dependencies.get_current_user",
                  new=AsyncMock(return_value=_admin_user())):
        auth_mock.return_value.require_auth = True
        auth_mock.return_value.setup_complete = True
        response = await gate.request()

        assert response.status_code == 200, response.text
        gate.witness.assert_called()


# ---------------------------------------------------------------------------
# Case 4 — setup mode and auth-disabled instances still reach every handler
# ---------------------------------------------------------------------------
class TestSetupModeStillAllowed:
    """No first-run path calls any TLS route, and the gate must not create one.

    Verified rather than assumed, the way beads i4qrp and 9kwzp.8 did:
    ``SetupPage.tsx`` imports exactly one API function (``completeSetup``) and
    contains no TLS reference at all; the only consumer of the thirteen
    ``api.ts`` TLS functions is ``TLSSettingsSection``, rendered from the
    post-auth Settings tab under the ``adminOnly`` ``tls-settings`` section;
    the ``mcp-server`` sidecar exposes no TLS tool; and ``tls/subprocess_proxy``
    forwards no ``/api/tls`` path. The renewal scheduler calls
    ``tls.renewal.renew_certificate`` in-process, not over HTTP.

    This still pins the auth-disabled behaviour, because an instance running
    with ``require_auth=False`` is a supported configuration and the gate must
    not lock its operator out of TLS setup.

    NARROWED BY BEAD jy006. This class used to parametrize a third posture,
    ``require_auth=False, setup_complete=True``, and assert 200 on all thirteen
    routes. That combination now means "auth is off AND this instance has an
    operator identity", and the PO decided that the ten
    ``RequireHumanAdminForTLSMaterial`` routes require a real human admin in
    it — installing a caller-supplied private key makes it the instance's TLS
    identity, which survives the operator turning authentication back on. The
    two postures left here both describe an instance with NO operator identity,
    where every gate in the router still no-ops.

    That third posture is now split by route in
    ``test_auth_disabled_owned_instance_splits_by_gate`` below, and the full
    jy006 matrix lives in
    ``test_jy006_auth_disabled_identity_primitives.py``.

    NARROWED AGAIN BY BEAD 2u4e0 (2026-08-15). ``POST /test-dns-provider`` was
    the one denied route still reachable anonymously in that third posture,
    because it rides ``RequireHumanAdminForOutboundTest`` with eleven siblings
    in other routers and jy006's decision named none of them. The PO closed the
    whole family, so eleven of the thirteen routes here now refuse an anonymous
    caller on an owned auth-disabled instance and only the two status reads do
    not. The two postures parametrized below are unaffected: both still
    describe an instance with NO operator identity.
    """

    @pytest.mark.parametrize("route", TLS_ROUTES, ids=_route_id)
    @pytest.mark.parametrize(
        "require_auth,setup_complete",
        [(True, False), (False, False)],
        ids=["setup-incomplete", "both"],
    )
    @pytest.mark.asyncio
    async def test_anonymous_caller_reaches_handler_in_setup_mode(
        self, async_client, route, require_auth, setup_complete
    ):
        with _Gate(async_client, route) as gate, \
                patch("auth.dependencies.get_auth_settings") as auth_mock:
            auth_mock.return_value.require_auth = require_auth
            auth_mock.return_value.setup_complete = setup_complete
            response = await gate.request()

            assert response.status_code == 200, response.text
            gate.witness.assert_called()

    @pytest.mark.parametrize("route", TLS_ROUTES, ids=_route_id)
    @pytest.mark.asyncio
    async def test_auth_disabled_owned_instance_splits_by_gate(
        self, async_client, route
    ):
        """beads jy006 and 2u4e0 — the line the PO drew, restated per route.

        On an auth-disabled instance that HAS an operator identity, this router
        splits: the eleven routes that touch credential material refuse an
        anonymous caller, while the two status reads, which disclose none, do
        not. Asserted as one parametrized sweep over the whole route table
        rather than as two lists, so a route that changes tier shows up here as
        a named failure instead of silently joining the other group.

        AMENDED BY BEAD 2u4e0. ``/test-dns-provider`` used to expect 200 here,
        which made this router self-contradictory: the probe that SPENDS the
        stored DNS-provider credentials was anonymous while ``GET /settings``,
        which discloses them masked, was refused. The PO closed the whole
        ``RequireHumanAdminForOutboundTest`` family on 2026-08-15, so the split
        is now exactly the MCP-verdict split, and the expectation below is
        derived from ``DENIED_ROUTES`` instead of naming the exception.
        """
        expected = 401 if route in DENIED_ROUTES else 200

        with _Gate(async_client, route) as gate, \
                patch("auth.dependencies.get_auth_settings") as auth_mock:
            auth_mock.return_value.require_auth = False
            auth_mock.return_value.setup_complete = True
            response = await gate.request()

            assert response.status_code == expected, response.text
