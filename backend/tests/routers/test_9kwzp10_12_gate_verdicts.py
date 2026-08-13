"""beads 9kwzp.10 and 9kwzp.12 — the last five gate verdicts from the
``RequireAdminIfEnabled`` audit, each proved against the principal it names.

WHY ONE FILE FOR TWO BEADS
--------------------------

They share this file for the reason they shared a branch: every verdict below
is one row of ``tests/test_admin_gate_inventory.py``, and splitting them would
mean two files racing the same two frozensets.

THE FIVE VERDICTS
-----------------

1. ``PATCH /api/settings/security`` (9kwzp.10 item 1) carried the PLAIN admin
   tier, which ADMITS this principal. It is the only field-specific writer of
   ``ssrf_outbound_mode``, the setting that decides which hosts every outbound
   path in ECM may reach. Gating the eleven sinks of i4qrp / 9kwzp.6 /
   9kwzp.7 while leaving their policy writable by the same principal was a
   partial control. -> ``RequireHumanAdminForOutboundPolicy``.

2. ``POST /api/backup/restore-dbas`` and ``/restore-dbas-saved`` (item 2)
   carried the plain tier while the three legacy ``/restore*`` endpoints
   carried the human-admin one. Reading beads kgz3k and 6n76m shows the plain
   tier was CORRECT when it was written: 6n76m's changelog entry names the
   DBAS restore as deliberately unchanged because it applied denylist-filtered
   settings to the DISPATCHARR upstream and never touched ECM's own
   ``settings.json``. Bead …-dfkbn item 4 then added
   ``dbas/importers/ecm_settings.py`` and the DBAS restore began writing ECM's
   own blob — excluding only the live Dispatcharr connection, install-local
   bookkeeping and redaction sentinels, so NOT the media-server base URLs, the
   notification credentials, the GH #473 safety caps or ``ssrf_outbound_mode``.
   The gate went stale when the capability grew. -> ``RequireHumanAdminIfEnabled``,
   reused: its 403 body already says "restore rewrites admin-only settings".

3. Sync-target CRUD (item 3) was filed as a DECISION, not a defect: all five
   routes were already admin-gated. Verdict — deny the WRITES, admit the
   reads. Bead jcj0f did ship create/update/delete as MCP tools, but a
   deliberately-exposed tool establishes product intent, not least privilege.
   A write names a remote host, stores the credentials this instance
   authenticates to it with, sets ``insecure``, and registers the target's
   ``dbas_sync_<id>`` scheduled task; the router's own docstring says the
   authoritative SSRF check runs at EXECUTE time, not at write time.

4. Cloud-target and alert-method CRUD (item 4) carried no dependency at all.
   Cloud targets take the same split as sync targets and for the same reason.
   Alert methods are denied on BOTH halves, because ``list_alert_methods`` and
   ``get_alert_method`` return ``AlertMethod.config`` verbatim and that blob
   holds the Discord webhook URL, the Telegram bot token and the SMTP password
   — the exact families 9ej7f withheld from this principal on GET
   /api/settings. ``GET /types`` is the lone plain-admin route: a static
   catalogue with no install data.

5. ``POST /api/settings/reset-stats`` (9kwzp.12) carried no dependency and
   deletes every row of seven statistics tables. Decided against its sibling
   ``restart-services``, which kept admitting the principal because it rebuilds
   services from already-saved settings. Nothing here is recoverable that way.
   -> ``RequireHumanAdminForStatisticsReset``.

WHAT THE WITNESS IS FOR
-----------------------

Every case asserts a witness — the first side effect the handler reaches past
its own validation — so a 403 is proved to be the GATE answering rather than a
handler that happened to fail, and a pass is proved to have reached the
handler rather than to have been short-circuited somewhere else. That is the
pattern bead 9kwzp.11 established.
"""
from pathlib import Path
from typing import Callable, Dict, NamedTuple
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.routing import APIRoute

import database
from config import DispatcharrSettings
from models import User


# The static MCP key the auth layer will recognize. Hyphenated words rather
# than a credential-shaped literal: nothing here depends on its shape, only on
# the runtime settings and the Authorization header agreeing.
MCP_KEY = "mcp-service-principal-9kwzp10"

# Placeholder credentials for the request bodies. Angle-bracket form per
# docs/pytest_conventions.md -> "Credential Fixtures in Security Tests": the
# SECRET regex's ``(?=\w+)`` lookahead means a value opening with ``<`` is
# never a scan candidate, and nothing here depends on their shape.
S3_KEY_PLACEHOLDER = "<synthetic-s3-access-key-9kwzp10>"
S3_SECRET_PLACEHOLDER = "<synthetic-s3-secret-9kwzp10>"
SYNC_TOKEN_PLACEHOLDER = "<synthetic-sync-target-token-9kwzp10>"
WEBHOOK_PLACEHOLDER = "<synthetic-discord-webhook-9kwzp10>"

DENIED = "denied"
ADMITTED = "admitted"

# A filename that satisfies ``routers.backup._BACKUP_ZIP_FILENAME_RE`` so the
# saved-restore handler gets past its allowlist and reaches its witness.
SAVED_ARTIFACT = "ecm-backup-2026-08-13_120000.zip"

# An id no fixture creates. The write handlers below 404 on it, which is fine:
# the witness proves the gate admitted the caller, and a 404 raised by the
# handler is itself proof the handler ran.
ABSENT_ID = 999_777


class _Case(NamedTuple):
    """One route, its intended MCP verdict, and how to prove the handler ran.

    ``witness`` names the module attribute patched to record the first side
    effect past validation. ``extra`` names the other attributes that must be
    patched for an admitted caller to reach that point; each is a
    ``(target, factory)`` pair so the mock is rebuilt per test.
    """

    method: str
    path: str
    verdict: str
    witness: str
    extra: tuple
    request: dict


def _settings_for_security_write() -> DispatcharrSettings:
    return DispatcharrSettings(
        url="http://dispatcharr:8000",
        username="operator",
        password="<synthetic-dispatcharr-password>",
    )


def _engine_mock() -> MagicMock:
    """A task engine whose ``run_task`` is awaitable.

    ``asyncio.create_task`` needs a real coroutine; a bare ``MagicMock`` raises
    TypeError inside the handler's try block and turns an admitted request into
    a 500, which would make the admitted assertions read as passes for the
    wrong reason.
    """
    engine = MagicMock()
    engine.run_task = AsyncMock(return_value=None)
    return MagicMock(return_value=engine)


def _saved_backups_dir() -> MagicMock:
    """A ``BACKUPS_DIR`` whose ``iterdir`` yields exactly one saved artifact."""
    entry = MagicMock()
    entry.is_file.return_value = True
    entry.name = SAVED_ARTIFACT
    backups_dir = MagicMock()
    backups_dir.iterdir.return_value = [entry]
    return backups_dir


def _method_types() -> MagicMock:
    return MagicMock(return_value=[{"type": "discord", "display_name": "Discord"}])


def _session_witness() -> MagicMock:
    """A ``get_session`` that records the call and returns a REAL test session.

    The ``async_client`` fixture repoints ``database._SessionLocal`` at the
    test engine, so wrapping the real factory keeps every handler working
    against the test database while still making the call observable.
    """
    return MagicMock(wraps=database.get_session)


CASES = (
    # --- 9kwzp.10 item 1 -----------------------------------------------
    _Case(
        "PATCH", "/api/settings/security", DENIED,
        witness="routers.settings.save_settings",
        extra=(
            ("routers.settings.get_settings",
             lambda: MagicMock(side_effect=_settings_for_security_write)),
            ("routers.settings.clear_settings_cache", MagicMock),
        ),
        request={"json": {"ssrf_outbound_mode": "public_only"}},
    ),
    # --- 9kwzp.10 item 2 -----------------------------------------------
    _Case(
        "POST", "/api/backup/restore-dbas", DENIED,
        witness="task_engine.get_engine",
        extra=(
            ("routers.backup._sweep_stale_restore_temps", MagicMock),
            ("routers.backup._stream_upload_to_temp",
             lambda: AsyncMock(return_value=Path("/tmp/9kwzp10-artifact.zip"))),
        ),
        request={"files": {"file": ("artifact.zip", b"<synthetic-artifact>",
                                    "application/zip")}},
    ),
    _Case(
        "POST", "/api/backup/restore-dbas-saved", DENIED,
        witness="task_engine.get_engine",
        extra=(("routers.backup.BACKUPS_DIR", _saved_backups_dir),),
        request={"json": {"filename": SAVED_ARTIFACT, "confirm_apply": False}},
    ),
    # --- 9kwzp.10 item 3 -----------------------------------------------
    _Case(
        "GET", "/api/sync-targets", ADMITTED,
        witness="routers.sync_targets.get_session", extra=(), request={},
    ),
    _Case(
        "GET", f"/api/sync-targets/{ABSENT_ID}", ADMITTED,
        witness="routers.sync_targets.get_session", extra=(), request={},
    ),
    _Case(
        "POST", "/api/sync-targets", DENIED,
        witness="routers.sync_targets.get_session",
        extra=(("routers.sync_targets.encrypt_credentials",
                lambda: MagicMock(return_value=b"<ciphertext>")),),
        request={"json": {
            "name": "gate-probe-sync-target",
            "base_url": "http://sync-target.example.com:9191",
            "credentials": {"token": SYNC_TOKEN_PLACEHOLDER},
        }},
    ),
    _Case(
        "PUT", f"/api/sync-targets/{ABSENT_ID}", DENIED,
        witness="routers.sync_targets.get_session", extra=(),
        request={"json": {"enabled": False}},
    ),
    _Case(
        "DELETE", f"/api/sync-targets/{ABSENT_ID}", DENIED,
        witness="routers.sync_targets.get_session", extra=(), request={},
    ),
    # --- 9kwzp.10 item 4, cloud targets --------------------------------
    _Case(
        "GET", "/api/cloud-targets", ADMITTED,
        witness="routers.cloud_targets.get_session", extra=(), request={},
    ),
    _Case(
        "POST", "/api/cloud-targets", DENIED,
        witness="routers.cloud_targets.get_session",
        extra=(("routers.cloud_targets.encrypt_credentials",
                lambda: MagicMock(return_value=b"<ciphertext>")),),
        request={"json": {
            "name": "gate-probe-cloud-target",
            "provider_type": "s3",
            "credentials": {
                "access_key_id": S3_KEY_PLACEHOLDER,
                "secret_access_key": S3_SECRET_PLACEHOLDER,
                "bucket": "gate-probe",
            },
        }},
    ),
    _Case(
        "PATCH", f"/api/cloud-targets/{ABSENT_ID}", DENIED,
        witness="routers.cloud_targets.get_session", extra=(),
        request={"json": {"enabled": False}},
    ),
    _Case(
        "DELETE", f"/api/cloud-targets/{ABSENT_ID}", DENIED,
        witness="routers.cloud_targets.get_session", extra=(), request={},
    ),
    # --- 9kwzp.10 item 4, alert methods --------------------------------
    _Case(
        "GET", "/api/alert-methods/types", ADMITTED,
        witness="routers.alert_methods.get_method_types",
        extra=(), request={},
    ),
    _Case(
        "GET", "/api/alert-methods", DENIED,
        witness="routers.alert_methods.get_session", extra=(), request={},
    ),
    _Case(
        "GET", f"/api/alert-methods/{ABSENT_ID}", DENIED,
        witness="routers.alert_methods.get_session", extra=(), request={},
    ),
    _Case(
        "POST", "/api/alert-methods", DENIED,
        witness="routers.alert_methods.get_session",
        extra=(
            ("routers.alert_methods.get_method_types", _method_types),
            ("routers.alert_methods.create_method",
             lambda: MagicMock(return_value=None)),
            ("routers.alert_methods.get_alert_manager", MagicMock),
        ),
        request={"json": {
            "name": "gate-probe-alert-method",
            "method_type": "discord",
            "config": {"webhook_url": WEBHOOK_PLACEHOLDER},
        }},
    ),
    _Case(
        "PATCH", f"/api/alert-methods/{ABSENT_ID}", DENIED,
        witness="routers.alert_methods.get_session",
        extra=(("routers.alert_methods.get_alert_manager", MagicMock),),
        request={"json": {"enabled": False}},
    ),
    _Case(
        "DELETE", f"/api/alert-methods/{ABSENT_ID}", DENIED,
        witness="routers.alert_methods.get_session",
        extra=(("routers.alert_methods.get_alert_manager", MagicMock),),
        request={},
    ),
    # --- 9kwzp.12 ------------------------------------------------------
    _Case(
        "POST", "/api/settings/reset-stats", DENIED,
        witness="routers.settings.get_session", extra=(), request={},
    ),
)

DENIED_CASES = tuple(c for c in CASES if c.verdict == DENIED)
ADMITTED_CASES = tuple(c for c in CASES if c.verdict == ADMITTED)

# The path each case's route is REGISTERED under, for cross-checking against
# the live app. ``ABSENT_ID`` cases request a concrete id; the route template
# is what FastAPI registers.
TEMPLATE = {
    f"/api/sync-targets/{ABSENT_ID}": "/api/sync-targets/{target_id}",
    f"/api/cloud-targets/{ABSENT_ID}": "/api/cloud-targets/{target_id}",
    f"/api/alert-methods/{ABSENT_ID}": "/api/alert-methods/{method_id}",
}


def _case_id(case: _Case) -> str:
    return f"{case.method} {case.path}"


class _Gate:
    """Patch one case's witness plus its supporting mocks, and issue a request."""

    def __init__(self, async_client, case: _Case):
        self._client = async_client
        self._case = case
        self.witness = (
            _session_witness()
            if case.witness.endswith(".get_session")
            else MagicMock()
        )
        if case.witness == "routers.alert_methods.get_method_types":
            self.witness = _method_types()
        if case.witness == "task_engine.get_engine":
            self.witness = _engine_mock()
        self._contexts = [patch(case.witness, new=self.witness)]
        self._contexts += [
            patch(target, new=factory()) for target, factory in case.extra
        ]

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
            self._case.method, self._case.path,
            **self._case.request, **kwargs,
        )


def _non_admin_user() -> User:
    return User(
        id=91001,
        username="regular-user",
        is_admin=False,
        is_active=True,
        auth_provider="local",
    )


def _admin_user() -> User:
    return User(
        id=91002,
        username="admin-user",
        is_admin=True,
        is_active=True,
        auth_provider="local",
    )


def _mcp_runtime_settings() -> DispatcharrSettings:
    return DispatcharrSettings(
        url="http://dispatcharr:8000",
        username="operator",
        password="<synthetic-dispatcharr-password>",
        mcp_api_key=MCP_KEY,
    )


# ---------------------------------------------------------------------------
# Coverage guards — a route in these routers cannot slip in without a verdict
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "prefix",
    ["/api/cloud-targets", "/api/sync-targets", "/api/alert-methods"],
)
def test_every_route_in_the_three_routers_has_a_recorded_verdict(prefix):
    """``test_admin_gate_inventory`` pins routes that HAVE a gate; before this
    bead, none of these three routers had one on its CRUD half, so an ungated
    route was invisible to it. Walk the live app instead."""
    from main import app

    live = {
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute) and route.path.startswith(prefix)
        for method in route.methods - {"HEAD", "OPTIONS"}
    }
    covered = {
        (c.method, TEMPLATE.get(c.path, c.path))
        for c in CASES if c.path.startswith(prefix)
    }
    # The ``/test`` verbs were gated by bead 9kwzp.6 and are pinned there.
    already_pinned = {
        ("POST", "/api/cloud-targets/test"),
        ("POST", "/api/cloud-targets/{target_id}/test"),
        ("POST", "/api/alert-methods/{method_id}/test"),
    }
    assert live == covered | (already_pinned & live)


def test_no_gated_path_is_auth_exempt():
    """Authentication was never the missing half here; authorization was."""
    from main import AUTH_EXEMPT_PATHS

    gated_prefixes = (
        "/api/cloud-targets", "/api/sync-targets", "/api/alert-methods",
        "/api/settings/security", "/api/settings/reset-stats",
        "/api/backup/restore-dbas",
    )
    assert not [
        p for p in AUTH_EXEMPT_PATHS
        if any(p.startswith(prefix) for prefix in gated_prefixes)
    ]


# ---------------------------------------------------------------------------
# Case 1 — the authenticated non-admin is refused everywhere
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("case", CASES, ids=_case_id)
@pytest.mark.asyncio
async def test_non_admin_is_refused(async_client, case):
    with _Gate(async_client, case) as gate, \
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
# Case 2 — the MCP service principal is refused where that is the verdict
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("case", DENIED_CASES, ids=_case_id)
@pytest.mark.asyncio
async def test_mcp_principal_is_refused(async_client, case):
    """``RequireAdminIfEnabled`` would ADMIT this principal.

    ``_build_mcp_service_principal`` sets ``is_admin=True``, so the ordinary
    admin tier closes only the non-admin half and leaves the automation
    credential able to do the thing each of these beads is about. This is the
    case that separates a real fix from one that reads as fixed in review.
    """
    with _Gate(async_client, case) as gate, \
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


@pytest.mark.parametrize("case", ADMITTED_CASES, ids=_case_id)
@pytest.mark.asyncio
async def test_mcp_principal_still_reaches_the_admitted_routes(async_client, case):
    """The reads stay on the plain admin tier, deliberately.

    The two destination routers mask every credential to its last four
    characters and ``GET /api/alert-methods/types`` is a static catalogue, so
    none of these three discloses a recoverable secret — and admitting them is
    what keeps the sidecar's inventory tools working while its create / update
    / delete tools are refused. Pinning it here means demoting either half is a
    deliberate edit rather than a copied dependency.
    """
    with _Gate(async_client, case) as gate, \
            patch("auth.dependencies.get_settings",
                  return_value=_mcp_runtime_settings()), \
            patch("auth.dependencies.get_auth_settings") as auth_mock:
        auth_mock.return_value.require_auth = True
        auth_mock.return_value.setup_complete = True
        response = await gate.request(
            headers={"Authorization": f"Bearer {MCP_KEY}"}
        )

        assert response.status_code != 403, response.text
        gate.witness.assert_called()


# ---------------------------------------------------------------------------
# The 403 body must name THIS surface, not a neighbouring one
# ---------------------------------------------------------------------------
_EXPECTED_DENIAL_PHRASE = {
    "/api/settings/security": "outbound-policy mode",
    "/api/backup/restore-dbas": "backup restore",
    "/api/backup/restore-dbas-saved": "backup restore",
    "/api/sync-targets": "outbound destination",
    f"/api/sync-targets/{ABSENT_ID}": "outbound destination",
    "/api/cloud-targets": "outbound destination",
    f"/api/cloud-targets/{ABSENT_ID}": "outbound destination",
    "/api/alert-methods": "alert methods",
    f"/api/alert-methods/{ABSENT_ID}": "alert methods",
    "/api/settings/reset-stats": "reset statistics",
}


@pytest.mark.parametrize("case", DENIED_CASES, ids=_case_id)
@pytest.mark.asyncio
async def test_denial_names_its_own_surface(async_client, case):
    """Reusing a sibling dependency would leave every assertion above green
    while sending every triage of the refusal at the wrong subsystem. That is
    the whole reason ``mcp_denial_detail`` is a per-call-site parameter, and
    the reason this bead added four constants rather than reusing one."""
    with _Gate(async_client, case) as gate, \
            patch("auth.dependencies.get_settings",
                  return_value=_mcp_runtime_settings()), \
            patch("auth.dependencies.get_auth_settings") as auth_mock:
        auth_mock.return_value.require_auth = True
        auth_mock.return_value.setup_complete = True
        response = await gate.request(
            headers={"Authorization": f"Bearer {MCP_KEY}"}
        )

    detail = response.json()["detail"]
    assert _EXPECTED_DENIAL_PHRASE[case.path] in detail

    # ...and must be a body no OTHER shipped gate would have produced. A
    # substring hunt would false-positive (the outbound-destination body
    # legitimately mentions TLS verification), so compare against the actual
    # bodies instead: whichever gate this route carries, the eight are
    # pairwise distinct and only one of them may match.
    from tests.test_admin_gate_inventory import _mcp_denial_detail
    from auth import (
        RequireHumanAdminForNotificationCredential,
        RequireHumanAdminForOutboundDestination,
        RequireHumanAdminForOutboundPolicy,
        RequireHumanAdminForOutboundTest,
        RequireHumanAdminForServiceCredential,
        RequireHumanAdminForStatisticsReset,
        RequireHumanAdminForTLSMaterial,
        RequireHumanAdminIfEnabled,
    )

    bodies = [
        _mcp_denial_detail(dep.dependency)
        for dep in (
            RequireHumanAdminIfEnabled,
            RequireHumanAdminForOutboundTest,
            RequireHumanAdminForServiceCredential,
            RequireHumanAdminForTLSMaterial,
            RequireHumanAdminForOutboundPolicy,
            RequireHumanAdminForOutboundDestination,
            RequireHumanAdminForNotificationCredential,
            RequireHumanAdminForStatisticsReset,
        )
    ]
    assert sum(detail == body for body in bodies) == 1


# ---------------------------------------------------------------------------
# Case 3 — the human admin reaches every handler
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("case", CASES, ids=_case_id)
@pytest.mark.asyncio
async def test_admin_reaches_every_handler(async_client, case):
    with _Gate(async_client, case) as gate, \
            patch("auth.dependencies.get_auth_settings") as auth_mock, \
            patch("auth.dependencies.get_current_user",
                  new=AsyncMock(return_value=_admin_user())):
        auth_mock.return_value.require_auth = True
        auth_mock.return_value.setup_complete = True
        response = await gate.request()

        assert response.status_code != 403, response.text
        gate.witness.assert_called()


# ---------------------------------------------------------------------------
# Case 4 — first-run / auth-disabled instances still reach every handler
# ---------------------------------------------------------------------------
class TestSetupModeStillAllowed:
    """No gate here may become a first-run lockout.

    Verified rather than assumed, the way beads i4qrp, 9kwzp.8 and 9kwzp.11
    did. There IS a genuine first-run caller in this set:
    ``SecurityFirstRunModal.tsx`` PATCHes ``/api/settings/security`` to record
    the operator's backup-destination choice. It is safe because
    ``require_admin_if_enabled`` returns before the admin check and before the
    MCP rejection whenever ``require_auth`` is false or ``setup_complete`` is
    false — and because that endpoint already carried an admin gate, so this
    bead changed only the MCP half of it. The other four surfaces have no
    pre-auth caller: none of their paths is in ``AUTH_EXEMPT_PATHS`` (asserted
    above), so a first-run instance reaches them only while auth is off.
    """

    @pytest.mark.parametrize("case", CASES, ids=_case_id)
    @pytest.mark.asyncio
    async def test_setup_incomplete_reaches_the_handler(self, async_client, case):
        with _Gate(async_client, case) as gate, \
                patch("auth.dependencies.get_auth_settings") as auth_mock:
            auth_mock.return_value.require_auth = True
            auth_mock.return_value.setup_complete = False
            response = await gate.request()

            assert response.status_code != 403, response.text
            gate.witness.assert_called()

    @pytest.mark.parametrize("case", CASES, ids=_case_id)
    @pytest.mark.asyncio
    async def test_auth_disabled_reaches_the_handler(self, async_client, case):
        with _Gate(async_client, case) as gate, \
                patch("auth.dependencies.get_auth_settings") as auth_mock:
            auth_mock.return_value.require_auth = False
            auth_mock.return_value.setup_complete = True
            response = await gate.request()

            assert response.status_code != 403, response.text
            gate.witness.assert_called()
